"""聊天框 `@` 文件引用 —— 离线单测（假库/假对象存储，不触碰真实 MySQL 与桶）。

口径（产品决策 2026-11-04）：**上云 = 备份，归档 = 资产**。
`@` 的候选只有归档资产（账号级、跨工作空间）与本会话工作区既有文件；自动上云的
会话产物副本属于容灾层，不摊进 @ 清单。

覆盖：
  M1  引用展开：asset → cloud_asset_id、session → path，且 attachments 里原有上传附件不被覆盖；
  M2  展开期把关：资产不存在/非本人 404、超附件上限 400、未知来源 400、合计超 5 个 400（
      都在发送前显式拒，不留到执行层才说"没这个文件"）；
  M3  执行层落盘：cloud_asset_id 从对象存储取回内容写入本会话 uploads/ 并归一成附件；
  M4  取不到内容必须报错（不外抛空附件、不静默跳过）；
  M5  候选清单：只列本人资产、按文件名过滤、来源空间显示 name 而非裸 id、
      新会话（conversation_id=0）不去查会话文件、会话文件读取失败不连带整个 @ 面板报错；
  M6  候选必须可被本轮消费：非附件白名单类型与超 20MB 一律不入清单（不给假选项）；
  M7  身份只信服务端：展开时用调用方传入的 user_id，不从请求体取。
"""
import os
import pathlib
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException

from backend.modules.mind.api import send_payload
from backend.modules.mind.api.chat import _MENTION_LIMIT, _mention_usable, list_chat_mention_files
from backend.modules.mind.multimodal import MAX_FILE_SIZE

USER_ID = 7
BIG = MAX_FILE_SIZE + 1


def _params(cloud_files=None, attachments=None):
    return types.SimpleNamespace(cloud_files=cloud_files or [], attachments=list(attachments or []))


# ── M1/M2 引用展开与前置校验 ────────────────────────────────────────────────

def test_asset_ref_expands_to_cloud_asset_id(monkeypatch):
    found = {}
    monkeypatch.setattr("backend.modules.mind.api.user_assets.find_asset",
                        lambda aid, uid: found.setdefault("hit", {"id": aid, "filename": "月度报告.xlsx", "size": 1024}))
    params = _params([{"source": "asset", "ref": "abc123"}])
    send_payload._apply_cloud_refs(params, USER_ID)
    assert params.attachments == [{"cloud_asset_id": "abc123", "filename": "月度报告.xlsx"}]
    # 属主身份来自服务端解析的可信值
    assert found.get("hit") is not None


def test_session_ref_expands_to_workspace_path(monkeypatch):
    params = _params([{"source": "session", "ref": "/workspace/uploads/x.png"}])
    send_payload._apply_cloud_refs(params, USER_ID)
    assert params.attachments == [{"path": "uploads/x.png"}]


def test_uploaded_attachments_are_not_overwritten(monkeypatch):
    uploads = [{"filename": "a.csv", "category": "table", "size": 7, "content": b"x,y"}]
    params = _params([{"source": "session", "ref": "reports/t.md"}], attachments=uploads)
    send_payload._apply_cloud_refs(params, USER_ID)
    assert params.attachments[0]["content"] == b"x,y"
    assert params.attachments[1] == {"path": "reports/t.md"}


def test_missing_or_foreign_asset_is_rejected(monkeypatch):
    monkeypatch.setattr("backend.modules.mind.api.user_assets.find_asset", lambda aid, uid: None)
    with pytest.raises(HTTPException) as e:
        send_payload._apply_cloud_refs(_params([{"source": "asset", "ref": "gone"}]), USER_ID)
    assert e.value.status_code == 404
    assert "重新 @ 选择" in e.value.detail       # 给可操作的下一步，不是裸报错


def test_oversize_asset_rejected_before_send(monkeypatch):
    monkeypatch.setattr("backend.modules.mind.api.user_assets.find_asset",
                        lambda aid, uid: {"id": aid, "filename": "big.xlsx", "size": BIG})
    with pytest.raises(HTTPException) as e:
        send_payload._apply_cloud_refs(_params([{"source": "asset", "ref": "x"}]), USER_ID)
    assert e.value.status_code == 400
    assert "big.xlsx" in e.value.detail          # 点名是哪个文件超限


def test_unknown_source_and_empty_ref_are_rejected():
    for item in ([{"source": "backup", "ref": "y"}, {"source": "", "ref": "y"}, {"source": "asset", "ref": " "}]):
        with pytest.raises(HTTPException) as e:
            send_payload._apply_cloud_refs(_params([item]), USER_ID)
        assert e.value.status_code == 400


def test_total_files_cap_covers_cloud_refs():
    uploads = [{"filename": f"{i}.csv", "content": b"x"} for i in range(send_payload.MAX_FILES_PER_REQUEST)]
    with pytest.raises(HTTPException) as e:
        send_payload._apply_cloud_refs(_params([{"source": "session", "ref": "a.md"}], attachments=uploads), USER_ID)
    assert "含 @ 引用的文件" in e.value.detail


def test_no_refs_leaves_attachments_untouched():
    params = _params([], attachments=[{"filename": "a.csv", "content": b"x"}])
    send_payload._apply_cloud_refs(params, USER_ID)
    assert len(params.attachments) == 1


def test_cloud_refs_require_trusted_identity():
    with pytest.raises(HTTPException) as e:
        send_payload._apply_cloud_refs(_params([{"source": "asset", "ref": "x"}]), 0)
    assert e.value.status_code == 401


# ── M3/M4 执行层落盘 ────────────────────────────────────────────────────────

def test_place_attachments_materializes_cloud_asset(tmp_path, monkeypatch):
    from backend.modules.mind.execution import secure_sdk

    seen = {}

    def fake_fetch(asset_id, user_id):
        seen["args"] = (asset_id, user_id)
        return "归档表.csv", b"city,sales\nSH,10"

    monkeypatch.setattr("backend.modules.mind.api.user_assets.fetch_asset_bytes", fake_fetch)
    task = types.SimpleNamespace(
        attachments=[{"cloud_asset_id": "a1", "filename": "归档表.csv"}],
        context=types.SimpleNamespace(user_id=USER_ID),
    )
    secure_sdk.place_attachments(task, types.SimpleNamespace(workspace=tmp_path))
    assert seen["args"] == ("a1", USER_ID)          # 属主校验用服务端身份
    out = task.attachments[0]
    assert out["filename"] == "归档表.csv" and out["category"] == "table"
    assert out["path"].startswith("uploads/") and out["size"] == len(b"city,sales\nSH,10")
    assert (tmp_path / out["path"]).read_bytes() == b"city,sales\nSH,10"


def test_place_attachments_fails_loud_when_asset_unreadable(tmp_path, monkeypatch):
    from backend.modules.mind.execution import secure_sdk

    def boom(asset_id, user_id):
        raise ValueError(f"归档资产内容不可用: {asset_id}")

    monkeypatch.setattr("backend.modules.mind.api.user_assets.fetch_asset_bytes", boom)
    task = types.SimpleNamespace(
        attachments=[{"cloud_asset_id": "a1", "filename": "x.csv"}],
        context=types.SimpleNamespace(user_id=USER_ID),
    )
    # 取不到内容必须让本轮显式失败，不能静默丢掉附件、让用户以为 Agent 拿到了文件
    with pytest.raises(ValueError):
        secure_sdk.place_attachments(task, types.SimpleNamespace(workspace=tmp_path))


# ── M5/M6 候选清单 ─────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self, rows, sink):
        self.rows, self.sink = rows, sink

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    """假连接：按 SQL 片段回不同行的资产查询只有一条，够用。"""

    def __init__(self, rows):
        self.rows, self.sqls = rows, []

    def cursor(self):
        return _Cursor(self.rows, self.sqls)

    def close(self):
        pass


def _patch_conn(monkeypatch, rows):
    import backend.common.db.metadata_db as mdb
    conn = _Conn(rows)
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: conn)
    return conn


ASSET_ROWS = [
    {"id": "a1", "name": "月度报告", "filename": "月度报告.xlsx", "category": "report", "size": 2048,
     "created_at": "2026-11-01 10:00:00", "origin_workspace_name": "全国数据"},
    {"id": "a2", "name": "取数 SQL", "filename": "q.sql", "category": "sql", "size": 120,
     "created_at": "2026-10-30 09:00:00", "origin_workspace_name": "华东数据"},
    {"id": "a3", "name": "超大图", "filename": "big.png", "category": "image", "size": BIG,
     "created_at": "2026-10-29 09:00:00", "origin_workspace_name": "华东数据"},
]


def test_mention_files_lists_only_usable_owned_assets(monkeypatch):
    monkeypatch.setattr("backend.modules.mind.api.user_assets.list_session_files",
                        lambda ws, conv, uid: [])
    conn = _patch_conn(monkeypatch, ASSET_ROWS)

    out = list_chat_mention_files(q="报告", conversation_id=0, workspace_id=0,
                                  user={"user_id": USER_ID})
    sql, params = conn.sqls[0]
    assert params[0] == USER_ID, "资产查询必须按属主过滤（fail-closed，不靠前端筛）"
    assert "LIKE" in sql and "报告" in params[-1]
    ids = [a["ref"] for a in out["assets"]]
    assert ids == ["a1"], f"只应留下白名单类型且未超限的资产，实得 {ids}"
    got = out["assets"][0]
    assert got["source"] == "asset" and got["origin_workspace_name"] == "全国数据"
    assert got["filename"] == "月度报告.xlsx", "展示用 name，不得把裸资产 id 当名称"


def test_new_conversation_skips_session_files(monkeypatch):
    _patch_conn(monkeypatch, [])
    called = []
    monkeypatch.setattr("backend.modules.mind.api.user_assets.list_session_files",
                        lambda *a: called.append(a))
    out = list_chat_mention_files(q="", conversation_id=0, workspace_id=301, user={"user_id": USER_ID})
    assert called == [], "新会话还没有工作区，不该去查会话文件"
    assert out == {"assets": [], "session_files": []}


def test_session_file_failure_does_not_break_panel(monkeypatch):
    _patch_conn(monkeypatch, ASSET_ROWS[:1])

    def boom(ws, conv, uid):
        raise RuntimeError("会话工作区不可用")

    monkeypatch.setattr("backend.modules.mind.api.user_assets.list_session_files", boom)
    out = list_chat_mention_files(q="", conversation_id=88, workspace_id=301, user={"user_id": USER_ID})
    assert [a["ref"] for a in out["assets"]] == ["a1"]
    assert out["session_files"] == [], "会话文件读不到只影响该栏，不得把整个 @ 面板打崩"


def test_session_files_are_normalized_for_mention(monkeypatch):
    _patch_conn(monkeypatch, [])
    monkeypatch.setattr("backend.modules.mind.api.user_assets.list_session_files",
                        lambda ws, conv, uid: [
                            {"path": "uploads/photo.png", "size": 900, "synced": True},
                            {"path": "notes.md", "size": 40, "synced": False},
                            {"path": "raw.json", "size": 40, "synced": False},
                        ])
    out = list_chat_mention_files(q="", conversation_id=88, workspace_id=301, user={"user_id": USER_ID})
    refs = [f["ref"] for f in out["session_files"]]
    assert refs == ["uploads/photo.png", "notes.md"], f".json 不在附件白名单，不该出现: {refs}"
    assert out["session_files"][0]["category"] == "image"


def test_mention_limit_bounds_payload(monkeypatch):
    rows = [{"id": f"a{i}", "name": f"f{i}", "filename": f"f{i}.md", "category": "file",
             "size": 10, "created_at": "", "origin_workspace_name": ""} for i in range(_MENTION_LIMIT + 5)]
    _patch_conn(monkeypatch, rows)
    monkeypatch.setattr("backend.modules.mind.api.user_assets.list_session_files", lambda *a: [])
    out = list_chat_mention_files(q="", conversation_id=0, workspace_id=0, user={"user_id": USER_ID})
    assert len(out["assets"]) == _MENTION_LIMIT


# ── M6 可用性判定 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,size,ok", [
    ("x.csv", 10, True),
    ("x.xlsx", 10, True),
    ("x.png", 10, True),
    ("x.glb", 10, True),
    ("x.sql", 10, False),      # 归档工具支持，但附件链路解析不了 → 不进 @ 候选
    ("x.html", 10, False),
    ("x.md", MAX_FILE_SIZE, True),
    ("x.md", BIG, False),
    ("x", 10, False),
])
def test_mention_usable_gate(name, size, ok):
    assert _mention_usable(name, size) is ok


def test_backup_layer_is_not_exposed_through_mention():
    """口径守卫：@ 的检索面只有归档资产 + 当前会话文件，不得枚举全量云端备份。

    会话产物的云空间镜像是容灾副本（自动、随账号），把它的 LIST 摊进 @ 清单会让
    每个会话的中间产物都混进"可复用资产"的选择面 —— 那是两件事，必须分开。
    """
    import inspect

    src = inspect.getsource(list_chat_mention_files)
    assert "adh_user_assets" in src
    assert "list_session_files" in src
    for backup_api in ("list_keys", "cloud_files", "build_session_prefix"):
        assert backup_api not in src, f"@ 检索不得直读对象存储备份层: {backup_api}"


# ── 入口必须真的接上（回归历史上"接口有、前端没接"的空承诺）─────────────────

def test_send_models_accept_cloud_files():
    """两条发送入口都要认 cloud_files，且缺省为空（不给旧调用加必填参数）。"""
    from backend.modules.mind.api.chat import SendMessageRequest
    from backend.modules.mind.api.pipeline import PipelineExecuteRequest

    for cls in (SendMessageRequest, PipelineExecuteRequest):
        assert "cloud_files" in cls.model_fields, f"{cls.__name__} 未接受 @ 引用字段"
        assert cls.model_fields["cloud_files"].get_default() in ([], None)
    req = SendMessageRequest(question="q", cloud_files=[{"source": "asset", "ref": "x"}])
    assert req.cloud_files == [{"source": "asset", "ref": "x"}]
    # JSON 请求仍不得携带 attachments（附件只走 multipart），但 @ 引用是 JSON 合法的
    assert SendMessageRequest(question="q").attachments == []


def test_send_endpoints_pass_trusted_identity_to_parser():
    """展开 @ 引用用的 user_id 只能来自服务端解析的身份（护栏 §2 身份 fail-closed）。"""
    import inspect

    for path in ("backend/modules/mind/api/chat.py", "backend/modules/mind/api/pipeline.py"):
        src = (pathlib.Path(__file__).resolve().parent.parent / path).read_text(encoding="utf-8")
        assert "parse_send_request(request, " in src
        assert 'user_id=int(user["user_id"])' in src, f"{path} 未把可信身份传给发送解析"


def test_frontend_wires_the_at_menu_to_send():
    """`@` 面板必须接进输入框并把引用随消息发出（不是只做了一半的候选接口）。"""
    root = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    chat = (root / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    store = (root / "stores" / "chatStore.ts").read_text(encoding="utf-8")
    assert "/chat/mention-files" in chat, "候选接口没接进输入框"
    assert "<CloudFileMenu" in chat and "mentionQueryOf" in chat
    assert "cloudFiles: cloudRefs" in chat, "选中的引用没随发送上送"
    assert "cloud_files: opts.cloudFiles.map" in store, "请求体没带 cloud_files"
