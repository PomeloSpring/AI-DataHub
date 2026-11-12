"""选层口径：默认执行层可配置、AS-BOT 绑层优先、层不可用必须显式报错。

事实基准（见 service.pick_active_layer / pick_layer_for_as_bot 文档）：
- 执行层**全局生效**：`adh_workspace_execution_layers` 的写入 API 已退役，表内行是冻结
  遗留数据，不得再当配置真值源（否则管理页改不到、却被运行时悄悄采信）；
- 默认层真值源 = `adh_execution_layers.is_default`，且 `manager.resolve_workspace_layer`
  与 service 共用同一实现（历史上两处各写一份，属双口径）；
- AS-BOT 未绑层 = 与改造前完全一致，所以本文件第一件事是锁"零变化"；
- AS-BOT 绑了层但层不可用 → 报错，不静默退回默认层（两条层的模型通道与计费口径不同，
  静默换层会让用户以为在用百炼直连、实际在耗 Credits）。
"""
import inspect
from unittest.mock import patch

import pytest

from backend.modules.mind.execution import service as exec_service

_WS = 301


def _layer(lid=8, status="active", **over):
    row = {"id": lid, "name": f"cli-{lid}", "display_name": f"执行层 {lid}",
           "layer_type": "cli", "status": status, "is_default": 0,
           "config": {"mode": "sdk", "cli_name": "claude"}}
    row.update(over)
    return row


# ── 默认执行层 ─────────────────────────────────────────────────────
def test_default_layer_wins_over_id_order():
    """默认层优先；没标默认才按 id 最小（与改造前实际生效结果一致）。"""
    rows = [_layer(7), _layer(8, is_default=1)]
    with patch.object(exec_service, "list_layers", return_value=rows), \
         patch.object(exec_service, "is_healthy_layer", return_value=True):
        assert exec_service.pick_active_layer(_WS)["id"] == 8


def test_without_default_falls_back_to_smallest_id():
    rows = [_layer(8), _layer(7)]
    with patch.object(exec_service, "list_layers", return_value=rows), \
         patch.object(exec_service, "is_healthy_layer", return_value=True):
        assert exec_service.pick_active_layer(_WS)["id"] == 8   # 保持 list_layers 顺序（已按 id 升序）


def test_default_layer_must_be_active():
    """停用层不能当默认：那会让所有未绑层的 AS-BOT 一起不可用。"""
    with patch.object(exec_service, "get_layer", return_value=_layer(8, status="inactive")):
        with pytest.raises(ValueError, match="未启用"):
            exec_service.set_default_layer(8)


def test_set_default_clears_previous_flag():
    """置默认必须互斥（全表至多一行为 1），且要失效选层/模型候选缓存。"""
    written = []
    with patch.object(exec_service, "get_layer", return_value=_layer(8)), \
         patch.object(exec_service, "execute_write",
                      side_effect=lambda sql, params=(): written.append(sql) or 1), \
         patch("backend.common.ttl_cache.exec_layer_cache") as cache:
        exec_service.set_default_layer(8)
    assert any("is_default = 0" in s for s in written)          # 先清
    assert any("is_default = 1" in s for s in written)          # 再设
    cache.invalidate.assert_called_once()


def test_default_layer_lookup_skips_inactive_and_unhealthy():
    """默认标记落在一条已停用的层上时，不能因为它而选出一个跑不通的层。"""
    rows = [_layer(7, status="active"), _layer(8, status="inactive", is_default=1)]
    with patch.object(exec_service, "list_layers", return_value=rows), \
         patch.object(exec_service, "is_healthy_layer", return_value=True):
        assert exec_service.pick_active_layer(_WS)["id"] == 7


def test_workspace_binding_table_is_not_consulted():
    """选层不再读退役的工作空间绑定表（冻结遗留数据不得当配置真值源）。"""
    rows = [_layer(7, is_default=1)]
    with patch.object(exec_service, "list_layers", return_value=rows), \
         patch.object(exec_service, "is_healthy_layer", return_value=True), \
         patch.object(exec_service, "get_workspace_layers",
                      side_effect=AssertionError("不该被调用")) as bound:
        assert exec_service.pick_active_layer(_WS)["id"] == 7
        assert exec_service.pick_layer_for_as_bot({"execution_layer_id": 7}, _WS)["id"] == 7
        bound.assert_not_called()


def test_manager_and_service_share_one_implementation():
    """manager 不再自己写第二份选层规则（双口径会漂成"问答跑通、别处找不到层"）。"""
    from backend.modules.mind.execution.manager import ExecutionLayerManager

    src = inspect.getsource(ExecutionLayerManager.resolve_workspace_layer)
    assert "pick_active_layer" in src
    assert "list_layers()" not in src


# ── AS-BOT 绑层 ────────────────────────────────────────────────────
def test_unbound_as_bot_uses_default_layer_unchanged():
    """未绑层 → 完全走默认层实现，行为零变化（回归锚点）。"""
    sentinel = _layer(7, is_default=1)
    with patch.object(exec_service, "pick_active_layer", return_value=sentinel) as pick:
        for as_bot in ({"execution_layer_id": 0}, {"execution_layer_id": None}, {}, None):
            assert exec_service.pick_layer_for_as_bot(as_bot, _WS) is sentinel
        assert pick.call_count == 4
        pick.assert_called_with(_WS)


def test_bound_layer_returns_that_layer():
    p1 = patch.object(exec_service, "get_layer", return_value=_layer(8))
    p2 = patch.object(exec_service, "is_healthy_layer", return_value=True)
    with p1, p2:
        got = exec_service.pick_layer_for_as_bot({"execution_layer_id": 8}, _WS)
    assert got["id"] == 8


def test_missing_layer_raises_not_fallback():
    with patch.object(exec_service, "get_layer", return_value=None), \
         patch.object(exec_service, "pick_active_layer") as pick:
        with pytest.raises(exec_service.LayerSelectionError, match="不存在"):
            exec_service.pick_layer_for_as_bot({"execution_layer_id": 99}, _WS)
        pick.assert_not_called()   # 报错路径绝不"顺手"退回默认层


def test_inactive_layer_raises_with_actionable_hint():
    with patch.object(exec_service, "get_layer", return_value=_layer(8, status="inactive")):
        with pytest.raises(exec_service.LayerSelectionError, match="已停用"):
            exec_service.pick_layer_for_as_bot({"execution_layer_id": 8}, _WS)


def test_unhealthy_layer_raises():
    with patch.object(exec_service, "get_layer", return_value=_layer(8)), \
         patch.object(exec_service, "is_healthy_layer", return_value=False):
        with pytest.raises(exec_service.LayerSelectionError, match="健康检查"):
            exec_service.pick_layer_for_as_bot({"execution_layer_id": 8}, _WS)


def test_as_bot_normalize_carries_layer_id():
    """AS-BOT 归一必须带出 execution_layer_id，否则前端选了也读不到（配置空承诺）。"""
    from backend.modules.mind.execution.as_bots import _normalize

    assert _normalize({"as_bot_key": "k", "execution_layer_id": 8})["execution_layer_id"] == 8
    assert _normalize({"as_bot_key": "k", "execution_layer_id": None})["execution_layer_id"] == 0
    assert _normalize({"as_bot_key": "k"})["execution_layer_id"] == 0


def test_scheduled_layer_pick_shares_one_implementation():
    """定时分析不再内联第二份选层规则。"""
    from backend.modules.mind.execution import scheduled_analysis

    src = inspect.getsource(scheduled_analysis._execution_layer)
    assert "pick_layer_for_as_bot" in src
    assert "get_default_external_layer" not in src   # 兜底逻辑只留在 service 一处


def test_layer_write_api_keeps_default_out_of_generic_update():
    """默认层只能走专用入口：混在通用 update 里会被一次误编辑翻掉全站通道。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    api = (root / "backend/modules/platform/api/execution_layers.py").read_text(encoding="utf-8")
    assert '@router.put("/{layer_id}/default")' in api
    svc = (root / "backend/modules/mind/execution/service.py").read_text(encoding="utf-8")
    upd = svc.split("def update_layer")[1].split("def record_health")[0]
    assert "is_default" not in upd
