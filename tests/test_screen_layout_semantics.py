"""版面语义通道（layout / layout_rows / gap / size / after）的行为锁。

锁的是三件事，缺一就是"用户要的"和"页面上演的"不是一回事：
① 编译出的矩形**必须首尾相接**（相邻卡列边界相同 → 视觉间距恰好 = 板级 gap）；
② 歧义**必须回抛**（未知大小词、after 指代不明、in_row 抢位、half+two_thirds 真冲突），
   不做包含式模糊、不自行挑一张压窄（宁缺勿错数）；
③ 未被点名的卡**不许静默丢**，存量高度不许被顺手改。
"""
import pytest

from backend.modules.mind.execution.sdk_tools import screen_tools as st

_GAP16 = {"design": {"page": {"gap": {bp: 16 for bp in st._BP_COLUMNS}}}}


def _specs(*cards):
    """create 通道口径：[{title, chart_type, layout}] → 带规格。"""
    return st._layout_specs([{"title": t, "chart_type": ct, "layout": lay}
                             for t, ct, lay in cards])


def _assert_seamless(rects, canvas_w=1920):
    """同一带内相邻卡必须共用列边界（等距的充要条件），跨带不参与判定。

    两把尺子一起量：像素首尾相接（编译口自己算的），以及投影到 64 列后没有空洞 ——
    后者才是用户眼睛看到的间距。
    """
    by_band = {}
    for r in rects:
        by_band.setdefault(r["band"], []).append(r)
    for band, cards in by_band.items():
        cards.sort(key=lambda r: r["x"])
        for a, b in zip(cards, cards[1:]):
            assert a["x"] + a["w"] == b["x"], f"{a['title']} 右缘 ≠ {b['title']} 左缘（第 {band} 带）"
        proj = st._project_columns(cards, canvas_w)
        holes = [b["left"] - a["col_end"] for a, b in zip(proj, proj[1:])]
        assert not any(h < 0 for h in holes), f"投影重叠：{holes}"
        report = st._spacing_report(cards, filters=_GAP16)
        assert report["uneven_gaps"] == [], f"第 {band} 带视觉间距不等：{report['gaps']}"


# ── ① 编译口：首尾相接 ────────────────────────────────────────────────
def test_five_fifths_compile_to_exact_column_split():
    """5 张 fifth：舍入溢出（13×5=65）按 64 列精确等分，且注明缘由（不静默改用户意图）。"""
    rows, _notes = _specs(("A", "line", {"size": "fifth"}), ("B", "line", {"size": "fifth"}),
                          ("C", "line", {"size": "fifth"}), ("D", "line", {"size": "fifth"}),
                          ("E", "line", {"size": "fifth"}))
    rects, meta = st._compile_rows(rows, filters=_GAP16)
    _assert_seamless(rects)
    assert sum(r["cols"] for r in rects) == st._BP_COLUMNS["screen"]
    assert max(r["cols"] for r in rects) - min(r["cols"] for r in rects) <= 1
    assert any("精确等分" in n for n in meta["notes"])


def test_default_band_equal_split_and_unnamed_size_are_backfilled():
    """没给 size 的卡按满宽等分并在 notes 说明（等分是缺省，不是猜大小）。"""
    rows, _notes = _specs(("A", "line", None), ("B", "line", None), ("C", "line", None))
    rects, meta = st._compile_rows(rows, filters=_GAP16)
    _assert_seamless(rects)
    assert [r["cols"] for r in rects] == [22, 21, 21]
    assert any("未指定大小" in n for n in meta["notes"])


def test_half_plus_two_thirds_is_a_real_conflict_and_asks():
    """half + two_thirds = 32+43 超过满宽 64：真冲突必须拒并给出路，不自行压窄某一张。"""
    rows, _ = _specs(("A", "line", {"size": "half"}), ("B", "line", {"size": "two_thirds"}),
                     ("C", "line", {"size": "half"}))
    with pytest.raises(st.ScreenIntentError) as exc:
        st._compile_rows(rows, filters=_GAP16)
    msg = str(exc.value.response)
    assert "哪张让位" in msg and "不要自行挑一张压窄" in msg


def test_vertical_step_and_gap_come_from_board_gap_only():
    """纵向行距恒为 ceil(gap/4)×4：gap=20 → 20px；这是渲染端事实，不是服务端偏好。"""
    rows, _ = _specs(("A", "line", {"band": 1}), ("B", "line", {"band": 2}))
    filters = {"design": {"page": {"gap": {bp: 20 for bp in st._BP_COLUMNS}}}}
    rects, meta = st._compile_rows(rows, filters=filters)
    assert meta["vertical_px"] == 20 and meta["gap"]["screen"] == 20
    assert rects[1]["y"] - (rects[0]["y"] + rects[0]["h"]) == 20


def test_compiled_rects_pass_the_spacing_audit_and_say_so():
    """编译产物喂回等距判据：even_spacing 必为 True（否则编译口与审计口径漂移）。"""
    rows, _ = _specs(("A", "line", {"size": "quarter"}), ("B", "line", {"size": "quarter"}),
                     ("C", "line", {"size": "half"}))
    rects, _meta = st._compile_rows(rows, filters=_GAP16)
    report = st._spacing_report(rects, filters=_GAP16)
    assert report["equal"] is True and report["uneven_gaps"] == []
    assert all(hole == 0 for _a, _b, hole, _px in report["gaps"])


# ── ② 歧义必回抛 ─────────────────────────────────────────────────────
def test_unknown_size_word_is_rejected_with_the_word_list():
    with pytest.raises(st.ScreenIntentError) as exc:
        _specs(("A", "line", {"size": "halfe"}))
    assert "不认识的大小" in str(exc.value.response)
    assert "two_thirds" in str(exc.value.response)


def test_cols_out_of_range_is_rejected():
    """cols:4 = 一行十六张，必然挤到读不清：区间下限挡住，不照单全收。"""
    with pytest.raises(st.ScreenIntentError):
        _specs(("A", "line", {"size": "cols:4"}))
    with pytest.raises(st.ScreenIntentError):
        _specs(("A", "line", {"size": "cols:80"}))


def test_unknown_layout_key_is_rejected():
    """把 row/width 当 band/size 静默忽略 = 用户看到的版面和说的不是一回事。"""
    with pytest.raises(st.ScreenIntentError) as exc:
        _specs(("A", "line", {"row": 2, "width": 30}))
    assert "不认识这些键" in str(exc.value.response)


def test_after_unmatched_lists_candidates_and_tells_to_ask():
    with pytest.raises(st.ScreenIntentError) as exc:
        _specs(("A", "line", None), ("B", "line", {"after": "销售趋势"}))
    msg = str(exc.value.response)
    assert "向用户确认" in msg and "销售趋势" in msg


def test_in_row_collision_is_rejected_not_resolved_by_guessing():
    with pytest.raises(st.ScreenIntentError) as exc:
        _specs(("A", "line", {"in_row": 1}), ("B", "line", {"in_row": 1}))
    assert "被多张卡同时占用" in str(exc.value.response)


def test_band_numbers_are_compressed_without_empty_bands():
    """band=5 不会留 4 个空带：连续化并在 notes 说明。"""
    rows, notes = _specs(("A", "line", {"band": 5}), ("B", "line", {"band": 7}))
    assert len(rows) == 2
    assert any("连续化" in n for n in notes)


def test_band_below_one_is_rejected():
    with pytest.raises(st.ScreenIntentError):
        _specs(("A", "line", {"band": 0}))


# ── ③ 改板不静默丢卡、不顺手改高度 ───────────────────────────────────
def _chart(cid, title, chart_type, x, y, w, h, **cfg):
    return {"id": cid, "name": title, "chart_type": chart_type,
            "position": {"x": x, "y": y, "w": w, "h": h}, "config": cfg}


def _dashboard(charts, gap=None):
    return {"filters": gap if gap is not None else _GAP16, "charts": charts}


def _geoms(charts):
    return [st._chart_geom(c) for c in charts]


def test_layout_rows_positions_keeps_every_card_and_is_seamless():
    charts = [_chart(1, "A", "line", 0, 32, 480, 300), _chart(2, "B", "line", 480, 32, 480, 300),
              _chart(3, "C", "bar", 960, 32, 960, 300),
              _chart(4, "D", "table", 0, 332, 1920, 400)]
    updates, new_geoms, _meta = st._layout_rows_positions(
        [[{"title": "D", "size": "full"}],
         [{"title": "A", "size": "half"}, {"title": "B"}, {"title": "C"}]],
        _geoms(charts), [], filters=_GAP16)
    assert {u["chart_id"] for u in updates} == {1, 2, 3, 4}      # 一张不落
    assert new_geoms == []
    _assert_seamless([{"title": str(u["chart_id"]), "band": u["position"]["y"], **u["position"]}
                      for u in updates])


def test_layout_rows_leftovers_are_appended_not_dropped():
    charts = [_chart(1, "A", "line", 0, 32, 960, 300), _chart(2, "B", "line", 960, 32, 960, 300),
              _chart(3, "C", "bar", 0, 332, 1920, 300)]
    updates, _new, meta = st._layout_rows_positions([[{"title": "A"}]], _geoms(charts), [],
                                                    filters=_GAP16)
    assert {u["chart_id"] for u in updates} == {1, 2, 3}
    assert any("未静默丢卡" in n for n in meta["notes"])


def test_layout_rows_empty_argument_is_rejected():
    charts = [_chart(1, "A", "line", 0, 32, 960, 300)]
    with pytest.raises(st.ScreenIntentError) as exc:
        st._layout_rows_positions([], _geoms(charts), [], filters=_GAP16)
    assert "gap" in str(exc.value.response)          # 只想改间距就别给 layout_rows，给出路


def test_layout_rows_rejects_duplicate_titles_instead_of_guessing():
    charts = [_chart(1, "订单量", "line", 0, 32, 960, 300),
              _chart(2, "订单量", "bar", 960, 32, 960, 300)]
    with pytest.raises(st.ScreenIntentError) as exc:
        st._layout_rows_positions([[{"title": "订单量"}]], _geoms(charts), [], filters=_GAP16)
    assert "同名" in str(exc.value.response)


def test_reflow_card_band_touches_only_its_own_band():
    charts = [_chart(1, "A", "line", 0, 32, 640, 300), _chart(2, "B", "line", 640, 32, 640, 300),
              _chart(3, "C", "bar", 1280, 32, 640, 300),
              _chart(4, "D", "table", 0, 332, 1920, 400)]
    placed, meta = st._reflow_card_band(_dashboard(charts), 1, size="half")
    assert meta["band"] == 1 and len(placed) == 3
    by_id = {p["chart_id"]: p["position"] for p in placed}
    assert by_id[1]["w"] == pytest.approx(960, abs=2)
    assert 4 not in by_id                                 # 只回目标带：第二带不写 = 逐字不动
    _assert_seamless([{"title": str(k), "band": v["y"], **v} for k, v in by_id.items()])


def test_reflow_after_moves_within_band_and_rejects_cross_band_reference():
    charts = [_chart(1, "A", "line", 0, 32, 640, 300), _chart(2, "B", "line", 640, 32, 640, 300),
              _chart(3, "C", "bar", 1280, 32, 640, 300),
              _chart(4, "D", "table", 0, 332, 1920, 400)]
    board = _dashboard(charts)
    placed, _meta = st._reflow_card_band(board, 1, after="B")
    order = sorted(placed, key=lambda p: p["position"]["x"])
    assert [p["chart_id"] for p in order][:3] == [2, 1, 3]
    with pytest.raises(st.ScreenIntentError) as exc:
        st._reflow_card_band(board, 1, after="D")        # D 在另一带 → 拒并指路 layout_rows
    assert "layout_rows" in str(exc.value.response)


def test_reflow_rejects_editor_pinned_page_layout():
    """存了 config.pageLayout 的卡由编辑器列格说了算：size 写进去不会生效 → 不假装成功。"""
    charts = [_chart(1, "A", "line", 0, 32, 640, 300),
              _chart(2, "B", "line", 640, 32, 640, 300, pageLayout={"screen": {"col": 1, "cols": 20}})]
    with pytest.raises(st.ScreenIntentError) as exc:
        st._reflow_card_band(_dashboard(charts), 1, size="half")
    assert "pageLayout" in str(exc.value.response)


def test_reflow_rejects_unknown_size_word():
    charts = [_chart(1, "A", "line", 0, 32, 960, 300), _chart(2, "B", "line", 960, 32, 960, 300)]
    with pytest.raises(st.ScreenIntentError):
        st._reflow_card_band(_dashboard(charts), 1, size="bigger")


def test_stored_band_heights_are_reused_verbatim():
    """整板都带存量高度 = 用户只动了大小/位置/间距：高度逐字沿用（本次没改高度）。"""
    charts = [_chart(1, "A", "line", 0, 32, 960, 300), _chart(2, "B", "line", 960, 32, 960, 300)]
    placed, meta = st._reflow_card_band(_dashboard(charts), 1, size="third")
    assert all(p["position"]["h"] == 300 for p in placed)
    assert meta["heights_kept"] is True


def test_baseline_report_carries_gap_and_column_identity():
    """版面基线是"以现有布局为基础"的事实源：四档 gap、每带富余列、众数列宽都要在。"""
    charts = [_chart(1, "A", "line", 0, 32, 480, 300), _chart(2, "B", "line", 480, 32, 480, 300),
              _chart(3, "C", "bar", 960, 32, 960, 300)]
    base = st._layout_baseline(_dashboard(charts))
    assert base["gap"]["screen"] == 16 and base["gap_vertical_px"] == 16
    assert base["columns"] == 64 and base["canvas_width"] == 1920
    assert base["rows"][0]["spare_cols"] == 0
    assert base["modal_card_cols"] == 16
    assert "空洞" in base["note"] or "position" in base["note"]
