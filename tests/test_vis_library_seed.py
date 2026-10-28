"""字模库 seed 迁移与前端白名单对齐（防「入库即剥」的隐形缺陷）。

背景：字模样式键有两处必须同时成立——
  1) 迁移 SQL 里写入 style_config 的键；
  2) 前端 sanitizeStyle/visualConfig 的白名单（VISUAL_KEYS / WIDGET_KEYS）。
任一侧漏登记，表现都是「在管理页选了字模，图上没反应」（.qoder/rules/ui-vis-library-style.md §2）。
本用例不复制白名单常量，而是直接从 frontend/src/lib/dashboardDesign.ts 解析，
保证「白名单只有一份事实源」，前端改键、seed 没跟上（或反之）都会在这里红。
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESIGN_TS = ROOT / "frontend" / "src" / "lib" / "dashboardDesign.ts"
MIGRATION = ROOT / "docker" / "mysql" / "vis_library_widget_style_migration.sql"


def _whitelist(name: str) -> set:
    """解析 `const NAME = ['a', 'b', ...]`（跨多行、含行内注释）"""
    body = DESIGN_TS.read_text(encoding="utf-8")
    block = re.search(rf"const {name} = \[(.*?)\];", body, re.S)
    assert block, f"dashboardDesign.ts 里找不到 {name} 白名单"
    # 去掉 // 与 /* */ 注释后按引号取键名
    cleaned = re.sub(r"//[^\n]*|/\*.*?\*/", "", block.group(1), flags=re.S)
    return set(re.findall(r"'([^']+)'", cleaned))


def _values_tuple(body: str, start: int):
    """从 `(` 开始扫描一个 VALUES 元组：元素为 NULL / '...' / 整数（本仓库 seed 无转义引号）。"""
    i, out, n = start + 1, [], len(body)
    while i < n:
        while i < n and body[i] in ' \n\t':
            i += 1
        if body[i] == ')':
            return out, i
        if body.startswith('NULL', i):
            out.append(None)
            i += 4
        elif body[i] == "'":
            j = body.index("'", i + 1)
            out.append(body[i + 1:j])
            i = j + 1
        elif body[i].isdigit():
            j = i
            while body[j].isdigit():
                j += 1
            out.append(body[i:j])
            i = j
        else:
            raise AssertionError(f"无法解析的 seed 值，位置 {i}: {body[i:i+40]!r}")
        while i < n and body[i] in ' \n\t':
            i += 1
        if body[i] == ',':
            i += 1
    raise AssertionError('VALUES 元组未闭合')


_COLUMNS = ('code', 'name', 'category', 'chart_type', 'style_config', 'description', 'source', 'is_builtin', 'sort_order')


def _rows() -> list:
    """从迁移 SQL 抽出字模行（不靠正则拼字段，避开多行 JSON 的贪婪陷阱）"""
    sql = MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    rows = []
    for m in re.finditer(r"\(\s*'(?:ws_|cs_table_)", body):
        values, _ = _values_tuple(body, m.start())
        assert len(values) == len(_COLUMNS), f"字段数不符：{values[:2]}"
        row = dict(zip(_COLUMNS, values))
        row["style_config"] = json.loads(row["style_config"])
        row["is_builtin"] = int(row["is_builtin"])
        rows.append(row)
    return rows


def test_migration_is_idempotent_upsert():
    """幂等：必须 ON DUPLICATE KEY UPDATE（规则 §4），且不用 INSERT IGNORE 掩盖冲突"""
    sql = MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert body.count("ON DUPLICATE KEY UPDATE") == body.count("INSERT INTO adh_vis_components") == 2
    assert "INSERT IGNORE" not in body


def test_codes_unique_and_json_valid():
    rows = _rows()
    assert len(rows) == 11, f"预期 2 套表值图 + 9 套控件样式，实得 {len(rows)}"
    assert len({r["code"] for r in rows}) == len(rows), "字模 code 重复"
    for r in rows:
        assert isinstance(r["style_config"], dict) and r["style_config"], f"{r['code']} style_config 为空"


def test_widget_style_keys_survive_frontend_whitelist():
    """控件字模的每个键都在 WIDGET_KEYS 里（不在就会被 sanitizeStyle 剥掉）"""
    widget_keys = _whitelist("WIDGET_KEYS")
    for r in [x for x in _rows() if x["category"] == "widget_style"]:
        keys = set(r["style_config"]["widgetStyle"])
        assert keys, f"{r['code']} 缺 widgetStyle 段"
        assert keys <= widget_keys, f"{r['code']} 用了未登记的控件键：{sorted(keys - widget_keys)}"


def test_widget_style_only_targets_widget_types():
    """控件样式的 chart_type 必须是 widget_*（与图表样式取值域不相交，混挂会互相覆盖）"""
    for r in _rows():
        if r["category"] != "widget_style":
            continue
        assert r["chart_type"] is None or r["chart_type"].startswith("widget_"), \
            f"{r['code']} 的 chart_type={r['chart_type']} 不是控件类型"


def test_table_value_keys_survive_visual_whitelist():
    """表值图样式的键都在 VISUAL_KEYS 里，且渲染侧确有消费点（防「登记了但没人读」）"""
    visual_keys = _whitelist("VISUAL_KEYS")
    table_rows = [x for x in _rows() if x["category"] == "chart_style"]
    assert {r["chart_type"] for r in table_rows} == {"table_value"}
    consumed = set()
    for r in table_rows:
        keys = set(r["style_config"]["config"])
        assert keys <= visual_keys, f"{r['code']} 用了未登记的图表键：{sorted(keys - visual_keys)}"
        consumed |= keys
    chart_ts = (ROOT / "frontend" / "src" / "components" / "DashboardChart.tsx").read_text(encoding="utf-8")
    for key in consumed:
        # 消费点允许两种形式：直接读 cfg.<key>，或经 resolveChartDesign 归入 visual.<key>（如 gridColor）
        hit = re.search(rf"cfg\??\.{key}\b", chart_ts) or re.search(rf"visual\??\.{key}\b", chart_ts)
        assert hit, f"表值图字模键 {key} 在 DashboardChart 里没有消费点（登记了没人读）"


@pytest.mark.parametrize("category", ["widget_style", "chart_style"])
def test_frontend_knows_the_category(category):
    """前端分类清单必须含本批分类，否则管理页/编辑器看不到（空承诺）"""
    api = (ROOT / "frontend" / "src" / "api" / "visLibrary.ts").read_text(encoding="utf-8")
    assert f"'{category}'" in api
    design = DESIGN_TS.read_text(encoding="utf-8")
    assert f"{category}:" in design or category == "chart_style", "COMPONENT_SLOT 未登记该分类"
