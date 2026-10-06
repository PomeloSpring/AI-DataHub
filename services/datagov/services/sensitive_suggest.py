"""敏感字段识别建议（列级），供人工确认后写入 `adh_sensitive_fields`。

## 为什么需要它
`adh_sensitive_fields` 里现存的 14 条是**全局精确列名**规则（`column_name='phone'`），
而真实列名带业务前缀：`patient_phone` / `charger_phone` / `charger_id_no` /
`patient_user_name`。精确匹配一列都命中不了——实测 `t_case_records` 49 列，
敏感策略 **0 列命中**，13 列疑似敏感全部裸奔。基线设计没错，是规则覆盖不到真实命名。

## 与 datagov 的 POST /scan 的关系
`/scan` 已有关键词扫描，但直接 `persist=true` 会把 `*_url`、`is_*` 标志位、
经纬度等一并写成 `mask_type='partial'`（注释里"图片地址"含"地址"就命中），
既误伤可用性又稀释了真敏感列。本模块提供**更严的识别**：

1. **排除优先**：URL / 标志位 / 计数 / 编码 / 主键外键 等命中即不算敏感；
2. **强模式**：手机号、邮箱、身份证、密码、银行卡 —— 词边界/后缀匹配，直接建议；
3. **弱模式**：姓名 / 住址 / 年龄性别等 —— 必须有**业务描述佐证**才算，避免"案例名称"被当人名；
4. **mask_type 分级**：密码/身份证 → `full`/`hash`，手机号/邮箱/姓名 → `partial`，
   不再一律 `partial`；
5. **表级标签加权**：`adh_tag_values` 的「敏感级别=含个人信息」是**人工已确认**的表，
   其列的建议置信度上调（这是标签驱动敏感基线的落点）。

## 纪律
本模块**只产建议，不写库**。写入必须走显式确认（`apply` 参数/接口），
因为敏感基线"对所有人生效含 admin"，静默写入等于静默减少所有人可见数据。
"""

from __future__ import annotations

import re
from typing import Optional

# 类别 → (建议 mask_type, sensitivity_level)
_CATEGORY_SPEC = {
    "password": ("full", "critical"),
    "id_card": ("hash", "high"),
    "bank_card": ("hash", "high"),
    "phone": ("partial", "high"),
    "email": ("partial", "medium"),
    "name": ("partial", "medium"),
    "address": ("partial", "medium"),
    "profile": ("partial", "low"),      # 年龄/性别/生日等低敏画像
    "credential": ("hash", "high"),     # 执照/证书/外部身份标识
}

# ── 布尔/标志位：绝对不敏感，**优先于强模式**判定 ─────────────
# 例：`force_change_password` 是"是否强制改密码"的标志（不是密码值），
# `is_confirm_email` 是"是否已确认邮箱"（不是邮箱）。它们名字里带着
# password/email，会被强模式误伤，故必须先剔。
_BOOL_PATTERNS = [
    r"^is_", r"_flag$", r"_status$", r"_enable(d)?$", r"^enable",
    r"^force_", r"^need_", r"^can_", r"^has_", r"^allow_", r"^should_",
    r"^require_", r"^support_", r"^allow",
]

# ── 排除：命中即不算敏感（只作用于弱模式，强模式优先于本组）─────
# 依据：URL / 计数 / 编码 / 主键外键 这些"看起来像敏感词"的列
# 实际不承载敏感值，脱敏它们只损失可用性。
_EXCLUDE_PATTERNS = [
    r"(^|_)url(s)?$",          # url / image_url / download_url / notify_urls
    r"^url(s)?(_\w+)?$",       # url / urls / url_hook
    r"^area_?code$",           # 手机区号（不是手机号）
    r"^longitude$", r"^latitude$", r"(^|_)lng$", r"(^|_)lat$",   # 经纬度
    r"_count$", r"_num$", r"_total$", r"_size$",        # 计数/大小
    r"^id$", r"(^|_)id$", r"_type$", r"_level$", r"_mode$",
    r"_version$", r"_time$", r"_date$", r"_at$",
    r"^create_by$", r"^update_by$", r"^del_flag$",
]

# ── 强模式：直接建议 ───────────────────────────────────────────────
_STRONG_PATTERNS = [
    ("password", [r"(^|_)passw(or)?d$", r"(^|_)pwd$", r"(^|_)secret(_key)?$", r"密码", r"口令"]),
    ("id_card", [r"(^|_)id_?no$", r"(^|_)id_?card(_no)?$", r"identity_?no$",
                 r"(^|_)cert_?no$", r"身份证", r"证件号"]),
    ("bank_card", [r"bank_?card", r"(^|_)card_?no$", r"银行卡", r"卡号"]),
    ("phone", [r"(^|_)phone$", r"(^|_)mobile$", r"(^|_)tel(_no)?$",
               r"(^|_)phone_?number$", r"手机", r"电话", r"联系方式"]),
    ("email", [r"(^|_)email(_address)?$", r"(^|_)mail(_addr)?$", r"邮箱", r"邮件"]),
]

# ── 弱模式：必须业务描述佐证（避免"案例名称/医院名称"被当人名）──────
# (类别, 列名模式, 描述必须含的词, 描述出现即排除的词)
_WEAK_PATTERNS = [
    ("name", r"(^|_)name$|(^|_)user_?name$|(^|_)person(_name)?$|(^|_)contact$",
     ["姓名", "名字", "联系人", "负责人", "法人", "医生", "医师", "病人", "患者", "操作人", "经办人"],
     ["名称", "标题", "型号", "品类", "产品名", "文件名", "变量名", "字段名"]),
    ("address", r"(^|_)address(_\w+)?$|(^|_)addr(_\w+)?$",
     ["住址", "地址", "所在地", "省", "市", "区", "县", "街道"],
     ["网址", "链接", "url", "IP地址", "ip 地址"]),
    ("profile", r"(^|_)age$|(^|_)sex$|(^|_)gender$|(^|_)birth(day)?(_\w+)?$",
     ["年龄", "性别", "出生", "生日"],
     []),
    ("credential", r"(^|_)license(_no|_code)?$|(^|_)cert(ificate)?(_no)?$|(^|_)openid$|(^|_)out_.*_id$",
     ["执照", "执业", "证书", "资质", "外部", "映射"],
     []),
]

_SENSITIVE_LEVEL_LABELS = {"含个人信息", "敏感", "个人信息"}

# 描述里出现这些词时，**描述侧**的强模式命中不作数（列名命中不受影响）。
# 例：`area_code` 描述"负责人手机区号"含"手机"，但它是区号不是手机号；
# `is_confirm_email` 描述"是否已确认邮箱"含"是否"，是标志位。
_DESC_NEGATORS = [
    "区号", "是否", "标志", "标志位", "类型", "状态", "编码", "单号", "编号",
    "流水", "序列", "次数", "数量", "计数", "开关", "选项", "字典",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def classify_column(column_name: str, comment: str = "", business_desc: str = "",
                    table_has_pii_label: bool = False) -> Optional[dict]:
    """判定单个列是否疑似敏感。返回 None = 不敏感。

    Args:
        column_name: 物理列名
        comment: 列注释（adh_column_metadata.column_comment）
        business_desc: 业务描述（adh_column_metadata.business_desc，常比注释更准）
        table_has_pii_label: 所在表是否已标「敏感级别=含个人信息」（人工确认过的表）
    """
    col = (column_name or "").strip()
    if not col:
        return None
    col_l = col.lower()
    desc = f"{comment or ''} {business_desc or ''}".strip()
    desc_l = desc.lower()

    # 0) 布尔/标志位先剔（优先于强模式）：名字里带 password/email 也不算敏感值
    if _matches_any(col_l, _BOOL_PATTERNS):
        return None

    # 1) 强模式优先于一般排除：词边界/后缀匹配，误伤率低（如 charger_id_no = 身份证）
    for category, pats in _STRONG_PATTERNS:
        if _matches_any(col_l, pats):
            mask, level = _CATEGORY_SPEC[category]
            return _build(col, category, mask, level, "强模式命中(列名)",
                          table_has_pii_label)
        # 描述命中时要排除限定语义（"手机区号"≠手机号，"是否已确认邮箱"≠邮箱）
        if _matches_any(desc_l, pats) and not _matches_any(desc_l, _DESC_NEGATORS):
            mask, level = _CATEGORY_SPEC[category]
            return _build(col, category, mask, level, "强模式命中(描述)",
                          table_has_pii_label)

    # 2) 排除：强模式没命中的，才用排除规则滤掉伪敏感（URL/标志位/计数…）
    if _matches_any(col_l, _EXCLUDE_PATTERNS):
        return None
    # 业务单号/编码（描述说了算，避免把 order_no/case_code 当证件号）
    if re.search(r"(^|_)no$|(^|_)code$", col_l) and _matches_any(
            desc_l, ["编码", "单号", "编号", "流水", "序列"]):
        return None

    # 3) 弱模式：列名像，但必须描述佐证
    for category, col_pat, desc_required, desc_excluded in _WEAK_PATTERNS:
        if not re.search(col_pat, col_l, re.IGNORECASE):
            continue
        if desc_excluded and _matches_any(desc_l, desc_excluded):
            continue          # 描述说明它不是敏感值（如"案例名称"）
        if not desc or not any(w in desc for w in desc_required):
            continue          # 无佐证不算，宁缺勿错
        mask, level = _CATEGORY_SPEC[category]
        return _build(col, category, mask, level, f"弱模式+描述佐证({desc[:16]})",
                      table_has_pii_label)

    return None


def _build(column: str, category: str, mask: str, level: str,
           reason: str, labelled: bool) -> dict:
    """表级已标「含个人信息」时置信度上调（人工确认优先于纯规则）。"""
    confidence = "high" if (labelled or level in ("critical", "high")) else "medium"
    return {
        "column_name": column,
        "category": category,
        "mask_type": mask,
        "sensitivity_level": level,
        "confidence": confidence,
        "reason": reason,
        "table_labelled_pii": labelled,
    }


def suggest_for_table(columns: list[dict], table_has_pii_label: bool = False) -> list[dict]:
    """对一张表的列批量出建议。

    Args:
        columns: [{"column_name":..., "column_comment":..., "business_desc":...}, ...]
    """
    out = []
    for c in columns or []:
        hit = classify_column(
            c.get("column_name") or "",
            comment=c.get("column_comment") or "",
            business_desc=c.get("business_desc") or "",
            table_has_pii_label=table_has_pii_label,
        )
        if hit:
            out.append(hit)
    return out


def pii_labelled_tables() -> set[str]:
    """返回已标「敏感级别=含个人信息」的表名（裸表名，与 adh_tag_values.entity_id 同口径）。"""
    from services.shared.common.db import execute_query
    rows = execute_query(
        "SELECT DISTINCT tv.entity_id AS tbl FROM adh_tag_values tv "
        "JOIN adh_tags t ON tv.tag_id = t.id "
        "JOIN adh_tag_categories c ON t.category_id = c.id "
        "WHERE c.name = '敏感级别' AND t.is_active = 1 "
        "  AND t.name IN (%s)" % ",".join(["%s"] * len(_SENSITIVE_LEVEL_LABELS)),
        tuple(_SENSITIVE_LEVEL_LABELS),
    ) or []
    return {str(r["tbl"]).strip() for r in rows if r.get("tbl")}
