"""用户级界面偏好服务（当前只有「动效预设」）。

设计口径：
- 表 `adh_user_preferences` 是**多批次共用**的一行：`cloud_sync_enabled` 由云空间批次拥有
  （读写在 mind/execution/session_cloud.py），本模块只拥有 `motion_preset_code` /
  `motion_override` 两列。**写入一律按列 upsert**（ON DUPLICATE KEY UPDATE 只更新自己的列），
  整行覆盖会把别人的偏好刷回默认值 —— 历史缺陷同类（幂等 upsert 覆盖别人拥有的列）。
- 预设编码的唯一权威是字模库 `adh_vis_components`（category='motion_preset'）：
  这里只校验「存在 + 在用（未下架未禁用）」，不在服务端复制一份效果名枚举 ——
  两处清单迟早漂移，段/键合法性由前端 lib/motion.ts 的白名单在渲染与写入口双向把关。
- NULL 的语义是**没设过**（= 跟随系统默认 mp_default），因此不给存量用户回填；
  读取时无行/无值都返回 None，由前端解析层落到默认档（行为零变化的那一半）。
- 数值范围与前端 MOTION_KEYS 护栏同口径（durationMs/staggerMs/maxStaggerMs/offsetPx），
  超范围直接拒收并说明原因：让脏数据入库再"渲染时回落默认"= 用户以为配上了，实际没生效。
"""

from __future__ import annotations

import json
import logging

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

MOTION_PRESET_CATEGORY = "motion_preset"

# 差量偏好：段 -> {键: (最小值, 最大值)}。只允许调时间/位移类旋钮，
# 效果名（enter.effect / chart.appear / ...）必须来自字模库预设，不接受用户自由填。
OVERRIDE_NUMBER_BOUNDS: dict[str, dict[str, tuple[int, int]]] = {
    "enter": {"durationMs": (0, 5000), "staggerMs": (0, 1000), "maxStaggerMs": (0, 5000), "offsetPx": (0, 200)},
    "chart": {"durationMs": (0, 5000)},
    "transition": {"durationMs": (0, 5000)},
    "reveal": {"staggerMs": (0, 1000)},
}


def _loads(val, default=None):
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def get_motion_preferences(user_id: int) -> dict:
    """读动效偏好：{preset_code, override}；无行/无值一律 None（= 跟随系统默认）。

    读表失败**不静默当成默认值**：返回 None 之外还打日志，调用方（API）把状态原样下发，
    前端显示"未加载"并给重试入口 —— 把读失败伪装成"已按默认呈现"就是降级掩盖。
    """
    if not user_id or int(user_id) <= 0:
        return {"preset_code": None, "override": None}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT motion_preset_code, motion_override FROM adh_user_preferences WHERE user_id=%s",
                        (int(user_id),))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"preset_code": None, "override": None}
    return {
        "preset_code": row.get("motion_preset_code"),
        "override": _loads(row.get("motion_override"), None),
    }


def _assert_preset_usable(code: str) -> None:
    """预设必须存在且在用（未下架、未禁用）—— 存一个选不中的编码等于「配了没反应」。"""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, is_active, is_enabled FROM adh_vis_components "
                "WHERE category=%s AND code=%s", (MOTION_PRESET_CATEGORY, code))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f"动效预设「{code}」不存在（可选清单见「系统配置 → UI 字模库 → 动效预设」）")
    if not row.get("is_active"):
        raise ValueError(f"动效预设「{row.get('name') or code}」已下架，请另选一个或在字模库重新上架")
    if row.get("is_enabled") == 0:
        raise ValueError(f"动效预设「{row.get('name') or code}」已禁用，请另选一个或在字模库重新启用")


def _validate_override(override: dict) -> dict:
    """差量校验：只允许白名单段里的数值键，范围与前端同口径；键名拼错直接拒。"""
    if not isinstance(override, dict):
        raise ValueError("动效差量偏好必须是对象，如 {\"enter\": {\"durationMs\": 300}}")
    cleaned: dict = {}
    for section, values in override.items():
        bounds = OVERRIDE_NUMBER_BOUNDS.get(section)
        if bounds is None:
            allowed = " / ".join(OVERRIDE_NUMBER_BOUNDS)
            raise ValueError(f"未知动效段「{section}」，可选：{allowed}")
        if not isinstance(values, dict):
            raise ValueError(f"动效段「{section}」必须是对象（只含数值旋钮）")
        out: dict = {}
        for key, raw in values.items():
            bound = bounds.get(key)
            if bound is None:
                allowed = ", ".join(bounds)
                raise ValueError(f"「{section}.{key}」不是可调配额，可选：{allowed}（效果名请在字模库预设里选）")
            try:
                num = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"「{section}.{key}」必须是数字，收到 {raw!r}")
            lo, hi = bound
            if not lo <= num <= hi:
                raise ValueError(f"「{section}.{key}」超出范围 {lo}~{hi}，收到 {raw}")
            out[key] = int(num) if float(num).is_integer() else num
        cleaned[section] = out
    return cleaned


def set_motion_preset(user_id: int, code: str | None) -> None:
    """保存动效预设；code=None 表示「跟随系统默认」（把列写回 NULL，不是删行）。"""
    if code:
        _assert_preset_usable(str(code))
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_user_preferences (user_id, motion_preset_code) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE motion_preset_code=VALUES(motion_preset_code)",
                (int(user_id), code or None))
        conn.commit()
    finally:
        conn.close()


def set_motion_override(user_id: int, override: dict | None) -> None:
    """保存差量偏好（只存用户显式改过的键）；override=None 表示清除差量，回到纯预设。"""
    cleaned = _validate_override(override) if override is not None else None
    payload = json.dumps(cleaned, ensure_ascii=False) if cleaned is not None else None
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_user_preferences (user_id, motion_override) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE motion_override=VALUES(motion_override)",
                (int(user_id), payload))
        conn.commit()
    finally:
        conn.close()
