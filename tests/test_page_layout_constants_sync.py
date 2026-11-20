"""版面几何的**双侧契约**：Python 侧镜像常量必须与前端渲染端逐值相等。

为什么锁常量而不是锁文案：AI 通道算「等距」判据（`_spacing_report`）吃的是前端的
列制事实（64/48/24 列、画布设计宽、gap 默认与上下限、PAGE_ROW）。任一侧改了而另一侧没改，
模型就会拿旧算式宣判新渲染「间距不均」—— 这种漂移单看任何一侧的代码都看不出问题，
所以把它变成会红的用例（与 `tests/test_cloud_md_redaction.py` 同类的跨层契约测试）。

同时锁住列投影的**行为**：事故板 380px 节距必须报出 16/46 交替，服务端产出的首尾相接
版面必须判 equal（这是「不得凭像素差宣称等距」这条硬规则的唯一凭据）。
"""

import importlib
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

screen_tools = importlib.import_module("backend.modules.mind.execution.sdk_tools.screen_tools")

TS_PATH = os.path.join(os.path.dirname(__file__), "..",
                       "frontend", "src", "lib", "dashboardPageLayout.ts")


def _ts_src() -> str:
    with open(TS_PATH, encoding="utf-8") as f:
        return f.read()


def _ts_map(src: str, name: str) -> dict:
    """抓 `export const NAME = { screen: 64, ... }`（单行对象字面量）。"""
    hit = re.search(r"export const %s[^=]*=\s*\{([^}]*)\}" % re.escape(name), src)
    assert hit, f"前端 {name} 常量结构变了，镜像契约需要人工核对（不再静默通过）"
    return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", hit.group(1))}


def _ts_num(src: str, name: str) -> int:
    hit = re.search(r"export const %s\s*(?::[^=]*)?=\s*(\d+)" % re.escape(name), src)
    assert hit, f"前端 {name} 常量结构变了，镜像契约需要人工核对"
    return int(hit.group(1))


def test_bp_columns_mirror_frontend():
    assert screen_tools._BP_COLUMNS == _ts_map(_ts_src(), "PAGE_COLUMNS")


def test_bp_widths_mirror_frontend():
    assert screen_tools._BP_WIDTHS == _ts_map(_ts_src(), "PAGE_WIDTHS")


def test_default_gap_mirror_frontend():
    # 前端 DEFAULT_PAGE_GAP = LEGACY_PAGE_GAP（换算存量布局专用，不能被用户新设置影响）
    src = _ts_src()
    assert screen_tools._DEFAULT_PAGE_GAP == _ts_map(src, "LEGACY_PAGE_GAP")
    assert re.search(r"export const DEFAULT_PAGE_GAP\s*=\s*LEGACY_PAGE_GAP", src), \
        "前端默认间距不再等于 LEGACY_PAGE_GAP，Python 镜像的取值来源要一起改"


def test_gap_bounds_mirror_frontend():
    src = _ts_src()
    assert screen_tools._GAP_MIN == _ts_num(src, "PAGE_GAP_MIN")
    assert screen_tools._GAP_MAX == _ts_num(src, "PAGE_GAP_MAX")


def test_row_unit_mirror_frontend():
    assert screen_tools._ROW_UNIT == _ts_num(_ts_src(), "PAGE_ROW")


# ── 列投影与等距判据（行为，不是文案） ────────────────────────────────────

def _row(xs_w, chart_type="big_number_trend", y=40, h=200):
    return [{"title": f"C{i}", "chart_type": chart_type, "x": x, "y": y, "w": w, "h": h}
            for i, (x, w) in enumerate(xs_w)]


def test_incident_pitch_reports_alternating_gaps():
    """事故板真实坐标（x=20/400/780/1160/1540，w=360）：节距 380px = 12.67 列 → 空洞。

    渲染端实际视觉间距是 16/46/46/16（不是模型宣称的「横向 20px 一致」）。
    """
    rects = _row([(20, 360), (400, 360), (780, 360), (1160, 360), (1540, 360)])
    report = screen_tools._spacing_report(rects, filters={})
    assert report["equal"] is False
    assert [g[3] for g in report["gaps"]] == [16, 46, 46, 16]
    assert [g[2] for g in report["gaps"]] == [0, 1, 1, 0]      # 列空洞数
    audit = screen_tools._layout_audit(rects, filters={})
    assert audit["even_spacing"] is False
    # 重叠/越界都没破，所以 ok 仍为真（历史板大量如此，判 fail 会刷屏）——但话术必须点名
    assert audit["ok"] is True
    assert "**不得**据此宣称间距一致" in audit["hint"]
    assert "不得向用户宣称间距相同" in screen_tools._spacing_notice(audit)


def test_seamless_tiling_is_even_and_says_so():
    """服务端 `_tile` 产出的首尾相接版面：空隙全等于板级 gap，判 equal。"""
    widths = screen_tools._tile(screen_tools._CANVAS_W, 5)
    xs, x = [], 0
    for w in widths:
        xs.append((x, w))
        x += w
    audit = screen_tools._layout_audit(_row(xs), filters={})
    assert audit["even_spacing"] is True
    assert [g[3] for g in audit["spacing"]["gaps"]] == [16, 16, 16, 16]
    assert "间距已按列投影核验" in screen_tools._spacing_notice(audit)


def test_vertical_gap_is_always_ceil_of_gap_by_row_unit():
    """CSS rowGap 恒为 0：纵向间距只来自渲染端 ceil(gap/PAGE_ROW) 行，手写像素差被忽略。"""
    rects = _row([(0, 960), (960, 960)], y=40, h=200)
    for gap, expected in ((16, 16), (20, 20), (18, 20), (0, 0), (64, 64)):
        report = screen_tools._spacing_report(rects, gap=gap)
        assert report["gap_vertical_px"] == expected, gap


def test_projection_handles_hole_and_shared_boundary():
    """空洞必须是 0 基右开边界之差：无缝平铺不得被算成 1 列空洞（历史误报）。"""
    rects = _row([(0, 960), (960, 960)])
    cols = screen_tools._project_columns(rects, screen_tools._CANVAS_W)
    assert [(c["left"], c["col_end"]) for c in cols] == [(0, 32), (32, 64)]
    assert cols[0]["col"] == 1 and cols[1]["col"] == 33          # 给人看的是 1 基
    report = screen_tools._spacing_report(rects, filters={})
    assert report["uneven_gaps"] == []


def test_board_without_page_gap_falls_back_to_frontend_default():
    assert screen_tools._board_gap_of({}) == screen_tools._DEFAULT_PAGE_GAP
    assert screen_tools._board_gap_of(None) == screen_tools._DEFAULT_PAGE_GAP
    assert screen_tools._board_gap_of(
        {"design": {"page": {"gap": {"mobile": 8, "nope": 99}}}})["mobile"] == 8
    # 未列出的档回落默认，不拿 0 当"没设"
    assert screen_tools._board_gap_of(
        {"design": {"page": {"gap": {"mobile": 8}}}})["screen"] == 16


def test_gap_input_is_clamped_or_rejected_never_guessed():
    gaps, err, note = screen_tools._clamp_gap_map(20)
    assert gaps == {"screen": 20, "desktop": 20, "tablet": 20, "mobile": 20} and not err and not note
    gaps, err, note = screen_tools._clamp_gap_map(300)
    assert gaps["screen"] == screen_tools._GAP_MAX and note       # 夹取必须留话，不静默改数
    assert screen_tools._clamp_gap_map({"screen": 24, "phone": 8})[1] is not None   # 未知档拒
    assert screen_tools._clamp_gap_map(True)[1] is not None
    gaps, _err, _note = screen_tools._clamp_gap_map({"screen": 32})
    assert gaps["desktop"] == 16                                    # 缺档取默认，不跟着 32 走


def test_merge_page_gap_preserves_human_layout_keys():
    """只替换 design.page.gap：人侧编排（canvas/componentSnapshots/themePack）与已有 version 不动。"""
    filters = {"theme": {"mode": "dark"},
               "design": {"canvas": {"width": 1600}, "componentSnapshots": {"3": {}},
                          "themePack": "tp_tech", "motionOverride": {"off": True},
                          "page": {"version": 3}}}
    merged, notes = screen_tools._merge_page_gap(filters, {"screen": 20, "desktop": 20,
                                                           "tablet": 20, "mobile": 20})
    design = merged["design"]
    assert design["canvas"] == {"width": 1600} and design["componentSnapshots"] == {"3": {}}
    assert design["themePack"] == "tp_tech" and design["motionOverride"] == {"off": True}
    assert design["page"]["version"] == 3 and not notes            # 已有 page：不重复报副作用
    assert design["page"]["gap"]["screen"] == 20
    assert merged["theme"] == {"mode": "dark"}
    fresh, notes2 = screen_tools._merge_page_gap({"theme": {}}, {"screen": 8, "desktop": 8,
                                                                 "tablet": 8, "mobile": 8})
    assert "version" not in fresh["design"]["page"]                # 不伪造 version
    assert notes2 and "缩略图" in notes2[0]                         # 首次出现 design.page → 报副作用


def test_canvas_width_mirrors_get_canvas():
    assert screen_tools._canvas_width_of({}) == screen_tools._CANVAS_W
    assert screen_tools._canvas_width_of({"design": {"canvas": {"width": 1440}}}) == 1440
    # 存量卡超出声明宽时取实际跨度（与前端 getCanvas 同式）
    rects = _row([(0, 960), (1400, 600)])
    assert screen_tools._canvas_width_of({"design": {"canvas": {"width": 1200}}}, rects) == 2000
    assert screen_tools._canvas_width_of({"design": {"canvas": {"width": 10}}}) == 320
