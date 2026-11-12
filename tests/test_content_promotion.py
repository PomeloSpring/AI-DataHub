"""报告中心 → 内容管理提升通道的离线回归（不连库、不连 HTTP）。

这条通道的出错代价都是"用户以为提交成功了，其实门户上什么都没有 / 或者提上去的东西读不出"，
所以断言全部钉在五条口径上（口径原文见 ``content_promote_service`` 模块 docstring）：

1. **归集**：同一份报告的后续版本必须落回**同一条内容条目**（``series_key`` 是唯一归集键），
   不能积成 N 篇同名内容；新报告才新建。归集键用**真实现**（``report_access.series_key``）算，
   替身不自己拼 —— 否则口径漂移在这里测不出来。
2. **提升只复制正文**：报表快照失效/正文不可用时拒得干脆，绝不"先提个空条目占位"。
3. **属主与越权**：不是本人的报告拒；快照收紧后连属主也读不到正文时，如实说明并指路重新生成。
4. **写前安全边界**：正文含内部标识在**写入之前**就拒（不先落库再等提交审核才发现）。
5. **半成功必须显式**：门禁没过时草稿确实已存 → 回执 ``submitted=False`` + ``gate_blocked``
   逐条原因；``update_article`` 的"审核中不能改"原样端给用户，不转述成"提交失败"。

外加响应体纪律：报表 id / 条目 id / revision id / object_key 都不进回执（护栏 §7），
对外只有 slug 与业务名。
"""
import json

import pytest

from backend.modules.platform.services import content_promote_service as ps
from backend.modules.platform.services import content_service as svc
from backend.modules.viz.services import report_access, report_service
from tests.test_content_workflow import FakeDB

USER = {"user_id": 9, "username": "analyst"}
BODY = "## 行业概览\n\n2026 年口内扫描仪出货量同比增长【两成】，拼接误差用 RMSD 表征。"


class PromoteDB(FakeDB):
    """内容替身 + 提升通道的归集键查询（``source_type`` / ``source_ref`` 只在服务内部可见）。"""

    def insert(self, sql, params=None):
        rid = super().insert(sql, params)
        if sql.startswith("INSERT INTO adh_content_revisions"):
            # 基座替身不存 change_note，而“提升来源写进版本说明”正是本批要验的事实
            self.revisions[rid]["change_note"] = tuple(params or ())[2]
        return rid

    def _query_rows(self, sql, params):
        if sql.startswith("SELECT id, slug, title, status, version, versioning") and "source_type=%s" in sql:
            row = next((a for a in self.articles.values()
                        if a.get("source_type") == params[0] and a.get("source_ref") == params[1]), None)
            if not row:
                return []
            return [{"id": row["id"], "slug": row["slug"], "title": row["title"],
                     "status": row["status"], "version": row["version"],
                     "versioning": row.get("versioning") or "keep_history",
                     "live_revision_id": row["live_revision_id"],
                     "head_revision_id": row["head_revision_id"]}]
        if sql.startswith("SELECT id, change_note"):
            rows = [r for r in self.revisions.values() if r["article_id"] == int(params[0])]
            return [{"id": r["id"], "change_note": r.get("change_note") or "",
                     "created_by": r["created_by"], "created_at": None,
                     "release_seq": r["release_seq"], "release_label": r["release_label"],
                     "released_at": r["released_at"]} for r in reversed(rows)]
        return super()._query_rows(sql, params)


@pytest.fixture
def db(monkeypatch):
    fake = PromoteDB()
    monkeypatch.setattr(svc, "execute_query", fake.query)
    monkeypatch.setattr(svc, "execute_write", fake.write)
    monkeypatch.setattr(svc, "execute_insert", fake.insert)
    monkeypatch.setattr(ps, "execute_query", fake.query)
    return fake


@pytest.fixture
def reports(monkeypatch):
    """报表侧替身：只换掉"取原始行 + 读正文"两处 IO，判据（归集键/has_body）用真实现。

    三个失败方向分开可控（它们各自的文案与状态码不同，合一个开关就测不出走的是哪条）：
    ``owner`` 属主、``snapshot`` 取数授权快照、``readable`` 对象存储里的正文。
    """
    holder = {"row": {"id": 501, "title": "2026 口腔扫描行业月报", "task_id": 11,
                      "owner_id": 9, "workspace_id": 3, "format": "markdown",
                      "source_type": "scheduled", "generation_status": "ready",
                      "content_object_key": "report/501.md"},
              "body": BODY.encode("utf-8"), "readable": True, "owner": True, "snapshot": True}

    def load_report(report_id):
        return dict(holder["row"], id=int(report_id)) if int(report_id) == int(holder["row"]["id"]) else None

    def read_bytes(row):
        if not holder["readable"]:
            raise ValueError("报告正文在对象存储里取不到了，请重新生成该报告")
        return holder["body"]

    monkeypatch.setattr(report_service, "load_report", load_report)
    monkeypatch.setattr(report_service, "read_report_content_bytes", read_bytes)
    monkeypatch.setattr(report_access, "_owner_matches", lambda row, user=None: holder["owner"])
    monkeypatch.setattr(report_access, "can_access_report",
                        lambda row, user=None, token=None: holder["owner"] and holder["snapshot"])
    return holder


def _promote(**over):
    payload = {"report_id": 501, "category": "whitepaper", "versioning": "keep_history"}
    payload.update(over)
    return ps.promote(payload, USER)


def _existing(db, reports, *, status="published", live_release=1, versioning="keep_history"):
    """已提升过一次的条目：同一归集键（task:11），线上第 1 期，head 指向那一版。"""
    aid = db.insert("INSERT INTO adh_content_articles (slug) VALUES (%s)",
                    ("2026-口腔扫描行业月报", "2026 口腔扫描行业月报", "上月摘要", 7,
                     json.dumps([]), "markdown", "", "", json.dumps([]), 0, 0, status,
                     None, "report", report_access.series_key(reports["row"]),
                     versioning, "analyst", "analyst"))
    rid = db.insert("INSERT INTO adh_content_revisions (article_id, body) VALUES (%s,%s)",
                    (aid, BODY, "第 1 期", "h", "analyst"))
    db.revisions[rid].update(release_seq=live_release or 0,
                             release_label="2026-09" if live_release else "",
                             released_at="2026-09-30 10:00:00" if live_release else None)
    db.articles[aid].update(head_revision_id=rid, live_revision_id=(rid if live_release else 0))
    return aid, rid


# ── 1. 归集：同键追加、新键建条目 ─────────────────────────────────────────────

def test_new_series_creates_draft_and_submits(db, reports):
    """这份报告还没提升过 → 新建条目并登记归集键（source_ref 是内部标识，不进回执）。"""
    out = _promote()
    aid = next(iter(db.articles))
    assert out["created"] is True and out["status"] == svc.STATUS_IN_REVIEW
    assert db.articles[aid]["source_type"] == "report"
    assert db.articles[aid]["source_ref"] == "task:11"      # 归集键按真实现算，后续版本才接得上
    assert "已新建内容草稿" in out["message"]


def test_same_series_appends_to_the_same_article(db, reports):
    """同归集键再提一次 → 追加到那一篇的 head，不新建第二篇同名内容。"""
    aid, rid = _existing(db, reports)
    reports["body"] = "## 行业概览\n\n10 月出货量继续爬升，返修率降到 1.2%。".encode("utf-8")
    out = _promote()
    assert out["created"] is False and out["slug"] == db.articles[aid]["slug"]
    assert len(db.articles) == 1, "同主题第二次提交不许变成两篇互不知情的条目"
    assert len(db.revisions) == 2, "新正文要成为 head 的一个新版本"
    assert db.articles[aid]["head_revision_id"] != rid
    assert db.articles[aid]["live_revision_id"] == rid, "线上仍是上一期，新内容不能绕过审核生效"
    assert "追加" in out["message"] and "尚未生效" in out["message"]


def test_append_records_promotion_provenance_in_change_note(db, reports):
    """提升产出的那一版必须能看出来自报告中心（版本清单里不得与手改混成一团）。"""
    aid, _ = _existing(db, reports)
    reports["body"] = "10 月：出货量继续爬升。".encode("utf-8")
    _promote()
    listed = svc.revision_list(aid)
    assert any("来自报告中心" in str(v.get("change_note") or "") for v in listed), \
        "版本说明要写下提升来源与时间"


def test_different_series_creates_new_article(db, reports):
    aid, _ = _existing(db, reports)
    reports["row"]["task_id"] = 22                        # 另一份报告（另一个归集键）
    _promote()
    assert len(db.articles) == 2
    assert db.articles[aid]["live_revision_id"], "上一篇不能被误改"


# ── 2. 只复制正文：报表侧不可用就干脆拒 ───────────────────────────────────────

def test_missing_report_is_404(db, reports):
    with pytest.raises(svc.ContentError) as exc:
        ps.promote({"report_id": 999, "category": "whitepaper"}, USER)
    assert exc.value.status == 404 and "不存在" in exc.value.detail
    assert not db.articles, "拒掉就是什么都没写"


def test_bodyless_report_is_refused_with_direction(db, reports):
    reports["row"]["content_object_key"] = ""             # 生成中/失败：还没有正文
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert exc.value.status == 409 and "正文" in exc.value.detail
    assert not db.articles


def test_vanished_object_is_refused(db, reports):
    reports["readable"] = False                           # 对象存储里取不到了（快照仍有）
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert exc.value.status == 409 and "重新生成" in exc.value.detail
    assert not db.articles


def test_expired_snapshot_is_refused_even_for_owner(db, reports):
    """数据权限收紧后快照失效：连属主也读不到正文，此时提升必然提上去一个读不到的条目。"""
    reports["snapshot"] = False
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert exc.value.status == 403 and "快照" in exc.value.detail
    assert not db.articles


def test_foreign_report_is_refused(db, reports):
    reports["owner"] = False
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert exc.value.status == 403 and "不属于当前账号" in exc.value.detail


# ── 3. 写前安全边界与门禁 ─────────────────────────────────────────────────────

def test_internal_identifier_in_body_is_refused_before_writing(db, reports):
    """正文带连接串/生成 SQL：写入之前就拒（不等提交审核），且不留半截内容。"""
    reports["body"] = "连接串 jdbc:mysql://10.0.0.1:3306/adh2 里查 select a from db.tbl".encode("utf-8")
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert exc.value.status == 422 and "内部标识" in exc.value.detail
    assert not db.articles and not db.revisions


def test_gate_block_keeps_draft_and_reports_each_reason(db, reports):
    """门禁没过（正文带占位）：草稿确实已存，回执必须两段讲清 —— 不许报成"提交失败/什么都没做"。"""
    reports["body"] = "本月返修率为【待填】%。".encode("utf-8")
    out = _promote()
    assert out["submitted"] is False
    assert out["gate_blocked"] and any("占位" in r for r in out["gate_blocked"])
    assert len(db.articles) == 1, "草稿要留着继续改，提升不替人做删除决定"
    assert db.articles[next(iter(db.articles))]["status"] == svc.STATUS_DRAFT
    assert "草稿已保存到内容中心" in out["message"] and "未能提交审核" in out["message"]


def test_in_review_article_refuses_append_with_original_reason(db, reports):
    """条目正在审核中 → ``update_article`` 那句原因原样端给用户，不转述、不静默改投新条目。"""
    aid, _ = _existing(db, reports, status=svc.STATUS_IN_REVIEW)
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert "审核中" in exc.value.detail
    assert len(db.revisions) == 1, "审核中不许先建一行没人引用的假历史"


def test_stale_version_is_409_not_overwrite(db, reports, monkeypatch):
    """提升必须把读到的 version 当乐观锁传下去：别人已改过就 409，不覆盖他的草稿。"""
    aid, _ = _existing(db, reports)
    fresh = dict(db.articles[aid])
    monkeypatch.setattr(ps, "_article_by_series",
                        lambda key: dict(fresh, version=int(fresh["version"]) - 1))
    with pytest.raises(svc.ContentError) as exc:
        _promote()
    assert exc.value.status == 409


# ── 4. 回执形状：期次事实 + 不外泄内部标识 ────────────────────────────────────

def test_append_with_identical_body_does_not_claim_a_new_version(db, reports):
    """正文一字不差时 `update_article` 不开新 revision —— 回执不能谎称“已追加为新版本”。

    实机报过：提两次同一份报告，版本清单里只有一条，回执却写着“已追加”。
    用户会拿着那句话去清单里找那不存在的一版。同时也不得把“第 0 期〈〉”这种算出来的空值丢给人看。
    """
    aid, rid = _existing(db, reports, live_release=0)
    out = _promote()
    assert out["created"] is False and out["new_version"] is False, out
    assert "一字不差" in out["message"] and "没有产生新版本" in out["message"]
    assert "第 0 期" not in out["message"] and "〈〉" not in out["message"], out["message"]
    assert len(db.revisions) == 1, "内容没变不多开一条假历史"


def test_append_actually_changes_body_reports_new_version(db, reports):
    aid, rid = _existing(db, reports)
    reports["body"] = "11 月：出货量持平。".encode("utf-8")
    out = _promote()
    assert out["new_version"] is True
    assert "第 1 期〈2026-09〉" in out["message"], out["message"]


def test_receipt_states_current_release_but_uses_no_internal_ids(db, reports):
    aid, _ = _existing(db, reports)
    reports["body"] = "10 月：出货量继续爬升，返修率 1.2%。".encode("utf-8")
    out = _promote()
    assert out["release"] == 1 and out["release_label"] == "2026-09", "回执要说清当前线上是哪一期"
    for key in ("id", "article_id", "revision_id", "report_id", "source_ref",
                "head_revision_id", "live_revision_id", "object_key"):
        assert key not in out, f"内部标识 {key} 不许进回执（护栏 §7）"
    assert out["versioning_label"] == "保留历史期次"


def test_promote_without_submit_leaves_draft(db, reports):
    out = _promote(submit=False)
    assert out["status"] == svc.STATUS_DRAFT and out["submitted"] is False
    assert out["gate_blocked"] == [] and "未提交审核" in out["message"]


def test_versioning_policy_follows_the_form(db, reports):
    _promote(versioning="latest_only")
    aid = next(iter(db.articles))
    assert db.articles[aid]["versioning"] == "latest_only"
    with pytest.raises(svc.ContentError) as exc:
        _promote(versioning="keep_all_history")           # 非法取值不许静默回默认
    assert "latest_only" in exc.value.detail


def test_html_report_lands_as_html_kind(db, reports):
    reports["row"]["format"] = "html"
    reports["body"] = "<section><h3>企业发展时间线</h3><p>2026 年 10 月。</p></section>".encode("utf-8")
    out = _promote()
    aid = next(iter(db.articles))
    assert db.articles[aid]["kind"] == svc.KIND_HTML
    assert out["slug"], "对外寻址用 slug"


# ── 5. 归集预判：点下去之前就要看见"追加还是新建" ─────────────────────────────

def test_preview_announces_append_target(db, reports):
    aid, _ = _existing(db, reports)
    view = ps.preview(501, USER)
    assert view["matched"]["slug"] == db.articles[aid]["slug"]
    assert view["matched"]["release_count"] == 1
    assert view["next_release_after"] == 1 and "追加" in view["reason"]
    assert "**" not in view["reason"], "reason 是直接渲染给人看的文案，不是 Markdown"
    assert view["kind"] == svc.KIND_MARKDOWN and view["body_chars"] == len(BODY)
    assert [c["key"] for c in view["categories"]], "栏目选择框要有候选（不能让人猜 key）"
    assert any(v["value"] == "latest_only" for v in view["versioning_options"])


def test_preview_announces_new_article(db, reports):
    view = ps.preview(501, USER)
    assert view["matched"] is None and view["next_release_after"] == 0
    assert "新建" in view["reason"]
    assert view["summary"], "摘要要给建议值，弹框里可改"


def test_preview_summary_drops_headings_and_table_rules(db, reports):
    reports["body"] = ("# 十月月报\n\n| 指标 | 值 |\n| --- | --- |\n"
                       "出货量同比 +20%，返修率 1.2%。").encode("utf-8")
    view = ps.preview(501, USER)
    assert "出货量同比" in view["summary"]
    assert "|" not in view["summary"] and "十月月报" not in view["summary"]


def test_html_summary_strips_tags_and_style_blocks_before_picking_lines(db, reports):
    """HTML 正文要**先剥标签再分行**。

    旧写法是先按行取前三段、最后才剥标签，实机抽出来的建议摘要长这样“， ，”：
    它会直接上卡片与搜索结果，比留空更坑（护栏：不给用户看不懂的半成品）。
    """
    reports["row"]["format"] = "html"
    reports["body"] = ("<html><head><style>.t{color:red;display:grid}</style></head>"
                       "<body><h2>十月总览</h2><table><tr><td>出货量</td><td>+20%</td></tr></table>"
                       "<p>直接可见度与装配误差均下降。</p></body></html>").encode("utf-8")
    view = ps.preview(501, USER)
    assert "style" not in view["summary"] and "<" not in view["summary"]
    assert "直接可见度" in view["summary"], view["summary"]
    assert "， ，" not in view["summary"]
