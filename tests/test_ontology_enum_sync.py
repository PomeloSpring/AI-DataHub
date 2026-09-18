"""本体 enum → 语义维度字典同步的单测: 解析规则与合并策略(不依赖真实 DB)。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from unittest.mock import MagicMock, patch

import pytest

from services.datacatalog.services.ontology_service import (
    _enum_item_to_label, sync_enums_to_dimensions,
)


# ── _enum_item_to_label 解析 ───────────────────────────────────

class TestEnumItemToLabel:
    @pytest.mark.parametrize("item, code, label", [
        ("0=FORM_ONLY（仅表单）", "0", "仅表单"),
        ("3=UPLOAD(已上传)", "3", "已上传"),
        ("5=COMPLETE", "5", "COMPLETE"),
        ("0：否", "0", "否"),
        ("1: 男", "1", "男"),
        ("2=SCAN_COMPLETED（扫描完成）", "2", "扫描完成"),
    ])
    def test_parses(self, item, code, label):
        assert _enum_item_to_label(item) == (code, label)

    @pytest.mark.parametrize("bad", ["", None, "无等号文本", "abc"])
    def test_rejects(self, bad):
        assert _enum_item_to_label(bad) is None


# ── sync_enums_to_dimensions 合并策略 ──────────────────────────

class FakeCursor:
    """最小光标: SELECT 返回预置维度行, UPDATE 记录; SELECT 结果按 (tbl,col) 匹配。"""

    def __init__(self, dim_rows):
        self.dim_rows = dim_rows          # [(id, table, column, name, aliases, value_labels)]
        self.updates = []
        self._result = []
        self._last_params = None

    def execute(self, sql, params=None):
        self._last_params = params or ()
        if sql.strip().upper().startswith("SELECT"):
            tbl, col = self._last_params[0], self._last_params[1]
            self._result = [
                {"id": i, "name": nm, "aliases": a, "value_labels": v, "description": ""}
                for (i, t, c, nm, a, v) in self.dim_rows if t == tbl and c == col
            ]
        elif sql.strip().upper().startswith("UPDATE"):
            self.updates.append((sql, self._last_params))

    def fetchall(self):
        return list(self._result)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _conn_for(cursor):
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor = MagicMock(return_value=cursor)
    return conn


DOC = {
    "objects": [{
        "key": "case",
        "properties": [
            {"column": "t_case_records.case_status", "name": "case_status",
             "description": "案例状态", "enum": ["0=FORM_ONLY（仅表单）", "3=UPLOAD(已上传)"]},
            {"column": "t_case_records.create_time", "name": "create_time",
             "description": "创建时间", "enum": []},
            {"column": "t_case_records.order_type", "name": "order_type",
             "description": "订单类型", "enum": ["1=普通", "2=加急"]},  # 字典缺行 → gap
        ],
    }],
}


class TestSyncMerge:
    def test_writes_labels_and_aliases(self):
        cur = FakeCursor([(10, "t_case_records", "case_status", "案例状态", None, None),
                          (11, "t_case_records", "create_time", "创建日期", '["旧别名"]', None)])
        with patch("services.datacatalog.services.ontology_service.get_metadata_conn",
                   return_value=_conn_for(cur)):
            res = sync_enums_to_dimensions(DOC, datasource_id=1)
        assert res["updated"] == 2
        writes = {p[1][-1]: p for p in cur.updates}  # UPDATE params 末位参数=维度 id
        # case_status: 写入 labels(同义别名均被过滤 → 不发 aliases 更新)
        sql10, p10 = writes[10]
        assert json.loads(p10[0]) == {"0": "仅表单", "3": "已上传"}
        assert "aliases" not in sql10
        # create_time: 只补别名(属性 description 进入 aliases), 无 enum 不写 labels
        sql11, p11 = writes[11]
        assert "aliases" in sql11 and "value_labels" not in sql11
        assert "旧别名" in p11[0] and "创建时间" in p11[0]

    def test_manual_labels_not_overwritten_and_gap(self):
        cur = FakeCursor([(10, "t_case_records", "case_status", "案例状态", None,
                           '{"0": "人工标签A", "9": "自定义"}')])
        with patch("services.datacatalog.services.ontology_service.get_metadata_conn",
                   return_value=_conn_for(cur)):
            res = sync_enums_to_dimensions(DOC, datasource_id=1)
        # 人工已有值保留("0"→人工标签A), 本体新码("3")补入; 差异记入 conflicts
        written = json.loads(cur.updates[0][1][0])
        assert written["0"] == "人工标签A" and written["3"] == "已上传"
        assert cur.updates[0][1][0]  # labels 列在先
        assert res["conflicts"] == [{"dimension_id": 10, "diff": {"0": "仅表单"}}]
        # order_type 有枚举但无字典行 → gap
        assert {"table": "t_case_records", "column": "order_type", "enum_size": 2} in res["gaps"]

    def test_idempotent_second_run(self):
        rows = [(10, "t_case_records", "case_status", "案例状态",
                 '["case状态"]', '{"0": "仅表单", "3": "已上传"}')]
        cur = FakeCursor(rows)
        with patch("services.datacatalog.services.ontology_service.get_metadata_conn",
                   return_value=_conn_for(cur)):
            res = sync_enums_to_dimensions(DOC, datasource_id=1)
        assert res["updated"] == 0  # labels/aliases 均已一致 → 不再发 UPDATE
