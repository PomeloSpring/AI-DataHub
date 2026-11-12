"""界面动效可配置（预设 + 用户偏好 + 看板挂载）的跨端口径守卫（源码级）。

口径（需求确认 + 计划《界面动效可配置》）：
  * 动效与主题**正交**：不写进 tp_* 主题包，也不参与 sanitizePack；
  * 默认 = 现状：`mp_default` 逐段等于现网实现，未落偏好/未挂字模时渲染零变化；
  * 分面裁决：Chat 与内容详情看用户偏好；看板/大屏播放看板上挂载；
    `prefers-reduced-motion` 永远赢且**可见标注**（不静默降级）；
  * 偏好落库跨设备，且与 `cloud_sync_enabled` **同表不同列**：写入必须按列 upsert。

守卫内容：
  M1  迁移 seed 六套内置预设齐全、幂等（INSERT IGNORE）、都是系统内置；
  M2  seed 的四段键与前端 MOTION_SECTION_KEYS 白名单逐项一致（漏登记=入库即剥）；
  M3  默认档逐段等于前端代码常量（改一处必改两处，否则开箱观感漂移）；
  M4  用户偏好迁移只加自己那两列、可重复执行、不给存量回填；
  M5  偏好 API 读写都带 motion 字段，且「这次没传字段」与「显式传 null」可区分；
  M6  服务端只按列 upsert（绝不写别人拥有的列），也不在 Python 里复制一份效果名枚举；
  M7  差量校验的实际行为（范围/段外键/效果名/非数字一律拒，合法值归一成 int）；
  M8  分类在前后端与看板挂载槽位三处同名登记（少一处就是"存了读不到"）；
  M9  六套内置预设入场通道互不重复（防"都是 fadeUp 变体、看不出差别"），且 v2 就地校准保留管理员自定义。
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED_SQL = ROOT / "docker" / "mysql" / "vis_library_motion_migration.sql"
PREF_SQL = ROOT / "docker" / "mysql" / "user_motion_preference_migration.sql"
MOTION_TS = ROOT / "frontend" / "src" / "lib" / "motion.ts"
DASHBOARD_TS = ROOT / "frontend" / "src" / "lib" / "dashboardDesign.ts"
VIS_API_TS = ROOT / "frontend" / "src" / "api" / "visLibrary.ts"
USERS_PY = ROOT / "backend" / "modules" / "auth" / "api" / "users.py"
SERVICE_PY = ROOT / "backend" / "modules" / "auth" / "services" / "user_preference_service.py"
VIS_SERVICE_PY = ROOT / "backend" / "modules" / "viz" / "services" / "vis_library_service.py"

EXPECTED_PRESETS = ["mp_default", "mp_none", "mp_soft", "mp_crisp", "mp_tech_flow", "mp_presentation"]


def _seed_rows() -> dict[str, dict]:
    """迁移里的 (code -> style_config)：seed 一行写不完，code 与 JSON 常在相邻行。"""
    text = SEED_SQL.read_text(encoding="utf-8")
    pairs = re.findall(r"\('(mp_\w+)',.*?'(\{\"enter\".*?\})'", text, re.S)
    return {code: json.loads(payload) for code, payload in pairs}


def _front_section_keys() -> dict[str, list[str]]:
    """解析 lib/motion.ts 的 MOTION_SECTION_KEYS（唯一白名单事实源）。"""
    block = re.search(r"MOTION_SECTION_KEYS = \{(.*?)\} as const", MOTION_TS.read_text(encoding="utf-8"), re.S)
    assert block, "lib/motion.ts 里找不到 MOTION_SECTION_KEYS，护栏失效"
    out: dict[str, list[str]] = {}
    for section, keys in re.findall(r"(\w+): \[([^\]]*)\]", block.group(1)):
        out[section] = re.findall(r"'([^']+)'", keys)
    return out


# ── M1/M2/M3 预设 seed ────────────────────────────────────────────────────────
def test_seed_has_six_builtin_presets_and_is_idempotent():
    text = SEED_SQL.read_text(encoding="utf-8")
    assert "INSERT IGNORE INTO adh_vis_components" in text, "seed 必须幂等（可重复执行）"
    rows = _seed_rows()
    assert sorted(rows) == sorted(EXPECTED_PRESETS), f"内置预设清单漂移: {sorted(rows)}"
    for code in EXPECTED_PRESETS:
        line = re.search(rf"\('{code}', '[^']*', 'motion_preset', NULL,", text)
        assert line, f"{code} 未按 motion_preset 分类登记"
    # 内置行必须是 system + is_builtin（只有 admin 可改），否则会被人当自定义件随手删
    assert text.count("'system', 1,") == len(EXPECTED_PRESETS)


def test_seed_keys_match_frontend_whitelist():
    keys = _front_section_keys()
    assert sorted(keys) == ["chart", "enter", "reveal", "transition"]
    for code, row in _seed_rows().items():
        assert sorted(row) == sorted(keys), f"{code} 段清单与白名单不齐"
        for section, allowed in keys.items():
            assert sorted(row[section]) == sorted(allowed), f"{code}.{section} 键与白名单不齐（漏登记=入库即剥）"


def test_default_preset_equals_frontend_constant():
    """mp_default 是「行为零变化」的锚点：seed 与代码常量必须逐段相同。"""
    ts = MOTION_TS.read_text(encoding="utf-8")
    block = re.search(r"DEFAULT_MOTION: MotionSpec = \{(.*?)\n\};", ts, re.S)
    assert block, "找不到 DEFAULT_MOTION"
    flat = " ".join(block.group(1).split())
    for fragment in ["enter: { effect: 'fadeUp', durationMs: 450", "easing: 'standard', staggerMs: 90",
                     "maxStaggerMs: 450, offsetPx: 10 }", "chart: { appear: 'default'",
                     "transition: { effect: 'none'", "reveal: { effect: 'none'"]:
        assert fragment in flat, f"DEFAULT_MOTION 少了「{fragment}」"
    seeded = _seed_rows()["mp_default"]
    assert seeded["enter"]["durationMs"] == 450 and seeded["enter"]["offsetPx"] == 10
    assert seeded["chart"]["appear"] == "default", "默认档必须不往 G2 spec 写 animate"
    assert seeded["transition"]["effect"] == "none" and seeded["reveal"]["effect"] == "none"


# ── M4 用户偏好迁移 ───────────────────────────────────────────────────────────
def test_preference_migration_adds_only_its_own_columns():
    text = PREF_SQL.read_text(encoding="utf-8")
    assert "motion_preset_code VARCHAR(64) NULL" in text
    assert "motion_override JSON NULL" in text
    # 幂等：MySQL 不支持 ADD COLUMN IF NOT EXISTS，必须先查 information_schema
    assert text.count("FROM information_schema.COLUMNS") >= 2
    assert "PREPARE" in text and "DEALLOCATE PREPARE" in text
    # 不回填：NULL = 跟随系统默认，回填等于替全部用户改了观感
    assert not re.search(r"^\s*UPDATE\s+adh_user_preferences", text, re.M | re.I)
    # cloud_sync_enabled 只出现在建表定义与说明里，本批不得改它的定义
    assert "ADD COLUMN cloud_sync_enabled" not in text
    assert "DROP" not in text.upper()


def test_migration_comments_point_at_real_files():
    """注释里的路径也要能打开：指向不存在的文件比没注释更容易误导后来的人。"""
    text = PREF_SQL.read_text(encoding="utf-8")
    for rel in re.findall(r"(backend/[\w/]+\.py)", text):
        assert (ROOT / rel).exists(), f"迁移注释指向不存在的文件: {rel}"


# ── M5/M6 偏好 API 与写入边界 ─────────────────────────────────────────────────
def test_preferences_api_reads_and_writes_motion_fields():
    api = USERS_PY.read_text(encoding="utf-8")
    assert "@router.get(\"/me/preferences\")" in api and "@router.put(\"/me/preferences\")" in api
    for field in ["motion_preset_code", "motion_override"]:
        assert api.count(field) >= 2, f"{field} 只在一侧出现（读写不对称）"
    # 三态：没传字段 ≠ 显式传 null（清除差量），靠 model_fields_set 区分
    assert "model_fields_set" in api
    assert "没有需要保存的偏好项" in api


def test_motion_writes_are_column_scoped():
    service = SERVICE_PY.read_text(encoding="utf-8")
    updates = re.findall(r"ON DUPLICATE KEY UPDATE\s+(\w+)=VALUES\(\w+\)", service)
    assert updates == ["motion_preset_code", "motion_override"], f"upsert 列不唯一: {updates}"
    # 同表另一列属于别的批次：这里出现就是整行覆盖的老毛病复发
    assert "cloud_sync_enabled=" not in service.replace(" ", "")
    assert "DELETE FROM adh_user_preferences" not in service


def test_server_does_not_duplicate_effect_enum():
    """效果名清单只有一份（字模库 + 前端白名单）。服务端复制一份迟早漂移。"""
    service = SERVICE_PY.read_text(encoding="utf-8")
    for name in ["fadeUp", "slideDown", "wipeDown", "pathIn", "growInY", "sectionStagger"]:
        assert name not in service, f"服务端硬编码了效果名 {name}"


# ── M7 差量校验实际行为 ───────────────────────────────────────────────────────
def test_override_validation_bounds():
    from backend.modules.auth.services.user_preference_service import _validate_override

    assert _validate_override({"enter": {"durationMs": 300.0}}) == {"enter": {"durationMs": 300}}
    assert _validate_override({"enter": {"staggerMs": 0}}) == {"enter": {"staggerMs": 0}}, "0 是真值，不是「没填」"
    for bad, hint in [({"speed": {"durationMs": 1}}, "未知动效段"),
                      ({"enter": {"effect": "fadeUp"}}, "不是可调配额"),
                      ({"enter": {"durationMs": 99999}}, "超出范围"),
                      ({"enter": {"durationMs": "fast"}}, "必须是数字"),
                      ("nope", "必须是对象")]:
        try:
            _validate_override(bad)
        except ValueError as err:
            assert hint in str(err), f"{bad} 的报错文案不含「{hint}」: {err}"
        else:
            raise AssertionError(f"非法差量被放行: {bad}")


# ── M8 分类三处登记 ───────────────────────────────────────────────────────────
def test_motion_preset_category_registered_everywhere():
    assert "'motion_preset'" in VIS_API_TS.read_text(encoding="utf-8"), "前端分类清单未登记"
    assert '"motion_preset"' in VIS_SERVICE_PY.read_text(encoding="utf-8"), "后端分类清单未登记"
    design = DASHBOARD_TS.read_text(encoding="utf-8")
    assert re.search(r"motion_preset:\s*'motion'", design), "看板挂载槽位未登记（存进去没人读）"
    # 动效字模的 style_config 是四段结构，交给 sanitizeStyle 会被当未知键剥干净
    assert "sanitizeMotion" in design, "挂载快照未按动效口径净化（下架后快照兜底会失效）"
    # 主题包不得夹带动效段（正交：动效不参与 sanitizePack）
    assert not re.search(r"sanitizePack[\s\S]{0,400}['\"](enter|reveal)['\"]", design)


# ── M9 预设之间必须肉眼可辨（每套走不同入场通道） ────────────────────────────
V2_SQL = ROOT / "docker" / "mysql" / "vis_library_motion_v2_migration.sql"


def test_builtin_presets_use_distinct_entrance_channels():
    """历史缺陷：五档非默认预设都是 fadeUp 的时长变体（只差 10~20px 位移），用户分不出。
    现要求六套预设的 enter.effect 互不重复。"""
    rows = _seed_rows()
    channels = [rows[c]["enter"]["effect"] for c in EXPECTED_PRESETS]
    assert len(set(channels)) == len(channels), f"入场通道重复: {dict(zip(EXPECTED_PRESETS, channels))}"
    # 默认档仍是现状 fadeUp（默认零变化）
    assert rows["mp_default"]["enter"]["effect"] == "fadeUp"
    assert rows["mp_default"]["enter"]["durationMs"] == 450


def test_v2_migration_preserves_admin_edits_and_is_safe():
    """v2 就地校准内置预设参数：只认「通道仍是首版那条」的行，管理员改过就不动；
    不 DROP/DELETE，只更 style_config 与 description。"""
    assert V2_SQL.exists(), "缺 v2 校准迁移：已应用首版的库拿不到拉开后的参数"
    text = V2_SQL.read_text(encoding="utf-8")
    # 只拦真正的 DML/DDL 语句（列名 is_deleted 不算）
    assert not re.search(r"\bDELETE\s+FROM\b", text, re.I), "v2 不得含 DELETE FROM"
    assert not re.search(r"\bDROP\b", text, re.I), "v2 不得含 DROP"
    # 三条 UPDATE 均带「当前通道仍是首版那条」的保护条件
    updates = re.findall(r"UPDATE `adh_vis_components`[\s\S]*?;", text)
    assert len(updates) == 3, f"应只校准 3 档（soft/crisp/presentation），实得 {len(updates)}"
    for u in updates:
        assert "$.enter.effect" in u, "缺通道保护条件 → 会覆盖管理员自定义"
        assert "is_builtin` = 1" in u and "source` = 'system'" in u
    # 只改两列，不碰 cloud_sync 等他人拥有的列
    for u in updates:
        setcols = set(re.findall(r"`(\w+)` =", u.split("WHERE")[0]))
        assert setcols == {"style_config", "description"}, f"改了他列: {setcols}"
