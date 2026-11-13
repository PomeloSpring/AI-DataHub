"""内容中心 P0 的离线回归：不连库、不连 HTTP 服务。

覆盖的是这次新功能的**把关点**（出错代价：错内容直接对全员可见 / 线上版本被草稿污染）：

- 提交门禁：硬阻断逐条回原因且不许提交；软警告不拦但审核人必须逐条确认才能通过；
- 状态机：``draft → in_review → approved → published → archived`` 的合法转换，
  未审核不得发布（免审直发是显式例外，必须带原因）；``archived`` 可一键重新上架
  —— 放回的是下架时在线那一版（当初审过的），但下架后又改的 head 不得跟着上线；
- 自审限制：作者不能审核自己写的版本（admin 例外放行，但回执与审计必须显式标出“作者自审”）；
- 版本模型：编辑已发布内容只产生新 head revision，**线上 live 版本不受污染**；
- 乐观锁：``expected_version`` 不匹配返 409，不覆盖他人编辑；
- 响应体不外泄内部标识（``source_ref`` / ``cover_object_key`` / revision 指针）；
- 未开放形态（附件/提升）显式拒绝并说明批次，不做"存下来但渲染不出"的静默降级。

DB 用最小假实现按 SQL 形状分派（与 ``tests/test_release_notes.py`` 同风格：断言 SQL 与
参数，而不是真连库）。
"""
import json
import os
import re
from datetime import date, timedelta

import pytest

from backend.modules.platform.services import content_service as svc

_ASSIGNS = re.compile(r"(\w+)\s*=\s*%s")
# WHERE 后面紧跟 ORDER/LIMIT 或到结尾 = 拼了个空条件（MySQL 1064）—— 替身层全局拦截，
# 避免“FakeDB 不报句、真库 500”这类只有实机才能发现的缺陷（历史：内容管理页）。
_EMPTY_WHERE = re.compile(r"WHERE\s*(?:ORDER\b|LIMIT\b|OFFSET\b|$)")
# 同一类只有真库才报的缺陷：拿 MySQL 保留字当裸别名（实机踩过 `AS release` → 1064 → 500）。
# 替身按 SQL 形状分派，看不出错，所以这里按词判：出现保留字裸别名当场 fail。
_RESERVED_ALIAS = re.compile(
    r"\bAS\s+(release|order|group|key|range|match|interval|partition|usage|desc|read|call|load|option|status|local)\b",
    re.I)


class FakeDB:
    """按 SQL 形状分派的极简元库替身。"""

    def __init__(self, *, articles=None, revisions=None, categories=None, tags=None):
        self.articles = articles or {}          # id -> dict
        self.revisions = revisions or {}        # id -> dict
        self.categories = categories or {7: {"id": 7, "category_key": "whitepaper",
                                             "label": "技术白皮书", "is_active": 1}}
        self.tags = tags if tags is not None else ["扫描精度", "质量控制"]
        self.reviews = []
        self.files = {}                       # id -> dict（附件行）
        self.kb_sync = []                     # 语料水位线（每次尝试一行）
        self._next_id = 1000

    # ── 入口 ──
    def query(self, sql, params=None, fetchone=False):
        if _EMPTY_WHERE.search(sql):
            raise AssertionError(f"SQL 里 WHERE 后没有条件（真库会 1064）: {sql[:160]}")
        hit = _RESERVED_ALIAS.search(sql)
        if hit:
            raise AssertionError(f"别名列用了 MySQL 保留字 {hit.group(1)!r}（真库会 1064）: {sql[:160]}")
        rows = self._query_rows(sql, tuple(params or ()))
        if fetchone:
            return rows[0] if rows else None
        return rows

    def write(self, sql, params=None):
        params = tuple(params or ())
        if sql.startswith("UPDATE adh_content_articles SET"):
            return self._update_article(sql, params)
        if sql.startswith("UPDATE adh_content_revisions SET release_seq="):
            # _mark_release：只能给尚未分配过期次的行编号（CAS 式守卫，真库同口径）
            rev = self.revisions.get(int(params[2]))
            if not rev or int(rev["release_seq"] or 0):
                return 0
            rev["release_seq"], rev["release_label"] = int(params[0]), params[1]
            rev["released_at"] = "2026-11-12 00:00:00"
            return 1
        if sql.startswith("DELETE FROM adh_content_revisions"):
            self.revisions = {k: v for k, v in self.revisions.items() if v["article_id"] != params[0]}
            return 1
        if sql.startswith("DELETE FROM adh_content_files WHERE id="):
            return 1 if self.files.pop(int(params[0]), None) else 0
        if sql.startswith("DELETE FROM adh_content_files WHERE article_id="):
            self.files = {k: v for k, v in self.files.items() if v["article_id"] != int(params[0])}
            return 1
        if sql.startswith("DELETE FROM adh_content_reviews"):
            before = len(self.reviews)
            self.reviews = [r for r in self.reviews if int(r["article_id"]) != int(params[0])]
            return before - len(self.reviews)
        if sql.startswith("DELETE FROM adh_content_articles"):
            return 1 if self.articles.pop(params[0], None) else 0
        if sql.startswith("UPDATE adh_content_tag_vocab"):
            return 1
        return 1

    def insert(self, sql, params=None):
        params = tuple(params or ())
        self._next_id += 1
        if sql.startswith("INSERT INTO adh_content_articles"):
            row = _new_article(id=self._next_id, slug=params[0], title=params[1],
                               summary=params[2], category_id=params[3], tags=params[4],
                               kind=params[5], link_url=params[6], link_label=params[7],
                               refs=params[8], pinned=params[9], featured=params[10],
                               status=params[11], review_by=params[12],
                               source_type=params[13], source_ref=params[14],
                               versioning=params[15], created_by=params[16], updated_by=params[17])
            self.articles[self._next_id] = row
            return self._next_id
        if sql.startswith("INSERT INTO adh_content_revisions"):
            self._next_id += 1
            self.revisions[self._next_id] = {"id": self._next_id, "article_id": params[0],
                                             "body": params[1], "created_by": params[4],
                                             # 期次三列由发布动作分配，建行时一律是“未发布过”
                                             "release_seq": 0, "release_label": "",
                                             "released_at": None}
            return self._next_id
        if sql.startswith("INSERT INTO adh_content_reviews"):
            self.reviews.append({"article_id": params[0], "revision_id": params[1],
                                 "action": params[2], "comment": params[3],
                                 "gate_warnings": params[4], "actor": params[5]})
            return self._next_id
        if sql.startswith("INSERT INTO adh_content_files"):
            self._next_id += 1
            self.files[self._next_id] = {"id": self._next_id, "article_id": int(params[0]),
                                         "object_key": params[1], "filename": params[2],
                                         "mime": params[3], "size": int(params[4]),
                                         "sort": int(params[5]), "created_at": None}
            return self._next_id
        if sql.startswith("INSERT INTO adh_content_categories"):
            self._next_id += 1
            self.categories[self._next_id] = {"id": self._next_id, "category_key": params[0],
                                              "label": params[1], "is_active": 1}
            return self._next_id
        if sql.startswith("INSERT INTO adh_content_tag_vocab"):
            self.tags.append(params[0])
            return self._next_id
        return self._next_id

    def _filter_rows(self, sql, params):
        """按 SQL 里的谓词形状过滤条目（清单与 COUNT 走同一个替身口径）。"""
        rows = list(self.articles.values())
        if "a.status=%s" in sql:
            rows = [a for a in rows if a["status"] == params[0]]
        elif "a.status IN" in sql:
            wanted = set(params[:2])
            rows = [a for a in rows if a["status"] in wanted]
        elif "live_revision_id <> 0" in sql:
            rows = [a for a in rows if a["live_revision_id"] and a["status"] != "archived"]
        if "a.status<>%s" in sql:          # 编排面排除草稿（非 mine 作用域）
            drop = self._param_for(sql, "a.status<>%s", params)
            rows = [a for a in rows if a["status"] != drop]
        if "a.created_by=%s" in sql:      # mine 作用域（「我的草稿」按登录身份识别）
            who = self._param_for(sql, "a.created_by=%s", params)
            rows = [a for a in rows if a.get("created_by") == who]
        if "a.review_by IS NOT NULL AND a.review_by < CURDATE()" in sql:
            today = str(date.today())
            rows = [a for a in rows if a.get("review_by") and str(a["review_by"]) < today]
        if "a.category_id=%s" in sql:
            # 参数位按谓词前已出现的 %s 个数取：编排面恒定排除草稿后，params[0] 不再是栏目
            cid = self._param_for(sql, "a.category_id=%s", params)
            rows = [a for a in rows if int(a["category_id"]) == int(cid)]
        if "COALESCE(c.category_key, '')<>%s" in sql:
            key = self._param_for(sql, "COALESCE(c.category_key", params)
            bad = {c["id"] for c in self.categories.values() if c["category_key"] == key}
            rows = [a for a in rows if int(a.get("category_id") or 0) not in bad]
        if "COALESCE(a.category_id, 0)<>%s" in sql:
            bad_id = int(self._param_for(sql, "COALESCE(a.category_id", params))
            rows = [a for a in rows if int(a.get("category_id") or 0) != bad_id]
        return [self._with_last_archived(dict(r, category_label="技术白皮书")) for r in rows]

    @staticmethod
    def _param_for(sql, needle, params):
        """按谓词前已出现的 %s 个数定位参数位（多条件叠加时不得再假定 params[0]）。"""
        return params[sql[:sql.index(needle)].count("%s")]

    def _with_last_archived(self, row: dict) -> dict:
        """真库由 _SELECT 的相关子查询带出“下架时在线的那一版”与“期次三列”，替身按同样口径算一次。

        不注这几列就只会测出“can_republish 永远 False”、“release 永远 0”，
        把上架与期次分支的缺陷藏起来。
        """
        back = next((int(r["revision_id"]) for r in reversed(self.reviews)
                     if int(r["article_id"]) == int(row["id"]) and r["action"] == "archive"), 0)
        live = int(row.get("live_revision_id") or 0)
        rev = self.revisions.get(live) or {}
        return dict(row, last_archived_revision_id=back,
                    live_release_seq=int(rev.get("release_seq") or 0),
                    live_release_label=str(rev.get("release_label") or ""),
                    release_count=sum(1 for r in self.revisions.values()
                                      if int(r["article_id"]) == int(row["id"])
                                      and int(r.get("release_seq") or 0) > 0))

    # ── 分派 ──
    def _query_rows(self, sql, params):
        if sql.startswith("SELECT kb_name, doc_title FROM adh_content_kb_sync"):
            # 当前归属 = 最新一条成功行（与真库的 ORDER BY id DESC LIMIT 1 同口径）
            hit = next((r for r in reversed(self.kb_sync)
                        if r["article_id"] == int(params[0]) and r.get("state") == "synced"), None)
            return [dict(hit)] if hit else []
        if sql.startswith("SELECT COUNT(*) AS n FROM adh_content_articles"):
            return [{"n": len(self._filter_rows(sql, params))}]
        if sql.startswith("SELECT * FROM adh_content_articles WHERE id="):
            row = self.articles.get(int(params[0]))
            return [dict(row)] if row else []
        if sql.startswith("SELECT a.*, c.label"):
            if "a.review_by IS NOT NULL" in sql and "LIMIT %s" not in sql:
                # pending_reviews 的到期复核清单（无参数、固定 LIMIT 20）；
                # 内容管理的 expired 筛选带 LIMIT %s，走下面 _filter_rows 的同一谓词分支
                return []
            if " LIMIT %s" in sql:
                limit = params[-2] if "OFFSET" in sql else params[-1]
                return self._filter_rows(sql, params)[:int(limit)]
            if "WHERE a.id=" in sql:
                row = self.articles.get(int(params[0]))
            else:
                row = next((a for a in self.articles.values() if a["slug"] == params[0]), None)
            if not row:
                return []
            out = self._with_last_archived(dict(row))
            cat = self.categories.get(int(row.get("category_id") or 0))
            if cat:
                out["category_label"] = cat["label"]
            return [out]
        if sql.startswith("SELECT body FROM adh_content_revisions"):
            rev = self.revisions.get(int(params[0]))
            return [{"body": rev["body"]}] if rev else []
        if sql.startswith("SELECT release_seq, release_label, released_at FROM adh_content_revisions"):
            rev = self.revisions.get(int(params[0]))
            return [{"release_seq": rev["release_seq"], "release_label": rev["release_label"],
                     "released_at": rev["released_at"]}] if rev else []
        if sql.startswith("SELECT COALESCE(MAX(release_seq)"):
            m = max([int(r.get("release_seq") or 0) for r in self.revisions.values()
                     if r["article_id"] == int(params[0])] or [0])
            return [{"m": m}]
        if sql.startswith("SELECT COUNT(*) AS n FROM adh_content_revisions"):
            return [{"n": sum(1 for r in self.revisions.values()
                               if r["article_id"] == int(params[0])
                               and int(r.get("release_seq") or 0) > 0)}]
        if sql.startswith("SELECT id, release_seq, release_label, released_at FROM adh_content_revisions"):
            rows = [r for r in self.revisions.values()
                    if r["article_id"] == int(params[0]) and int(r.get("release_seq") or 0) > 0]
            return [{"id": r["id"], "release_seq": r["release_seq"],
                     "release_label": r["release_label"], "released_at": r["released_at"]}
                    for r in sorted(rows, key=lambda x: -int(x["release_seq"]))]
        if sql.startswith("SELECT release_seq AS release_no FROM adh_content_revisions"):
            rows = sorted((r for r in self.revisions.values()
                           if r["article_id"] == int(params[0]) and int(r.get("release_seq") or 0) > 0),
                          key=lambda x: int(x["release_seq"]))
            return [{"release_no": r["release_seq"]} for r in rows]
        if sql.startswith("SELECT id FROM adh_content_revisions WHERE article_id="):
            hit = next((r for r in self.revisions.values()
                        if r["article_id"] == int(params[0])
                        and int(r.get("release_seq") or 0) == int(params[1])), None)
            return [{"id": hit["id"]}] if hit else []
        if sql.startswith("SELECT created_by FROM adh_content_revisions"):
            rev = self.revisions.get(int(params[0]))
            return [{"created_by": rev["created_by"]}] if rev else []
        if sql.startswith("SELECT is_active FROM adh_content_categories"):
            cat = self.categories.get(int(params[0]))
            return [{"is_active": cat["is_active"]}] if cat else []
        if sql.startswith("SELECT id FROM adh_content_categories"):
            key = params[0]
            hit = next((c for c in self.categories.values()
                        if (str(key).isdigit() and c["id"] == int(key)) or c["category_key"] == str(key)), None)
            return [{"id": hit["id"]}] if hit else []
        if sql.startswith("SELECT id, tag, sort, is_active FROM adh_content_tag_vocab"):
            return [{"id": i + 1, "tag": t, "sort": i + 1, "is_active": 1}
                    for i, t in enumerate(self.tags)]
        if sql.startswith("SELECT tags FROM adh_content_articles"):
            return [{"tags": a["tags"]} for a in self.articles.values()
                    if a["live_revision_id"] and a["status"] != "archived"]
        if sql.startswith("SELECT COUNT(*) AS n FROM adh_content_categories"):
            return [{"n": len(self.categories)}]
        if sql.startswith("SELECT COUNT(*) AS n FROM adh_content_articles WHERE category_id="):
            return [{"n": sum(1 for a in self.articles.values()
                               if int(a.get("category_id") or 0) == int(params[0]))}]
        if sql.startswith("SELECT COUNT(*) AS n FROM adh_content_articles a"):
            return [{"n": len(self._filter_rows(sql, params))}]
        if sql.startswith("SELECT id, status, live_revision_id, versioning FROM adh_content_articles"):
            row = self.articles.get(int(params[0]))
            return [{"id": row["id"], "status": row["status"],
                     "live_revision_id": row["live_revision_id"],
                     "versioning": row.get("versioning") or "keep_history"}] if row else []
        if sql.startswith("SELECT status, live_revision_id FROM adh_content_articles"):
            row = self.articles.get(int(params[0]))
            return [{"status": row["status"], "live_revision_id": row["live_revision_id"]}] if row else []
        if sql.startswith("SELECT id, category_key, label"):
            return [dict(c) for c in self.categories.values() if c.get("is_active", 1)]
        if sql.startswith("SELECT category_id, live_revision_id FROM adh_content_articles"):
            row = self.articles.get(int(params[0]))
            return [{"category_id": row["category_id"], "live_revision_id": row["live_revision_id"]}] if row else []
        if sql.startswith("SELECT live_revision_id FROM adh_content_articles"):
            row = self.articles.get(int(params[0]))
            return [{"live_revision_id": row["live_revision_id"]}] if row else []
        if sql.startswith("SELECT id, object_key, filename FROM adh_content_files"):
            # 删除条目时要按 article 拉全部对象 key（附件对象必须跟着条目一起清）
            return [dict(f) for f in sorted(self.files.values(), key=lambda x: (x["sort"], x["id"]))
                    if f["article_id"] == int(params[0])]
        if sql.startswith("SELECT id, filename, mime, size, sort, created_at FROM adh_content_files"):
            return [dict(f) for f in sorted(self.files.values(), key=lambda x: (x["sort"], x["id"]))
                    if f["article_id"] == int(params[0])]
        if sql.startswith("SELECT object_key, filename, mime FROM adh_content_files"):
            f = self.files.get(int(params[0]))
            return [dict(f)] if f and f["article_id"] == int(params[1]) else []
        if sql.startswith("SELECT object_key, filename FROM adh_content_files"):
            f = self.files.get(int(params[0]))
            return [dict(f)] if f and f["article_id"] == int(params[1]) else []
        if sql.startswith("SELECT id FROM adh_content_articles WHERE id="):
            row = self.articles.get(int(params[0]))
            return [{"id": row["id"]}] if row else []
        if sql.startswith("SELECT id FROM adh_content_articles WHERE slug="):
            keep = next((a for a in self.articles.values()
                         if a["slug"] == params[0] and a["id"] != int(params[1])), None)
            return [{"id": keep["id"]}] if keep else []
        if sql.startswith("SELECT id, change_note"):
            return [{"id": r["id"], "change_note": "", "created_by": r["created_by"],
                     "created_at": None, "release_seq": r["release_seq"],
                     "release_label": r["release_label"], "released_at": r["released_at"]}
                    for r in self.revisions.values() if r["article_id"] == int(params[0])]
        if sql.startswith("SELECT action, comment"):
            return [{"action": v["action"], "comment": v["comment"], "gate_warnings": v["gate_warnings"],
                     "actor": v["actor"], "actor_channel": "human", "revision_id": v["revision_id"],
                     "created_at": None}
                    for v in reversed(self.reviews) if v["article_id"] == int(params[0])]
        raise AssertionError(f"FakeDB 未覆盖的 SQL: {sql[:120]}")

    def _update_article(self, sql, params):
        set_part = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        cols = _ASSIGNS.findall(set_part)
        where = sql.split(" WHERE ", 1)[1]
        if where.startswith("id=%s AND version=%s"):
            article_id, expect_version = params[-2], params[-1]
            values = params[:len(cols)]
            row = self.articles.get(int(article_id))
            if not row or int(row["version"]) != int(expect_version):
                return 0            # 乐观锁：版本不匹配 = 零行受影响
        else:
            article_id, values, expect_version = params[-1], params[:len(cols)], None
            row = self.articles.get(int(article_id))
            if not row:
                return 0
        for col, val in zip(cols, values):
            row[col] = val
        for lit in re.findall(r"(\w+)\s*=\s*(?!%s)([\w()\'\s,]+?)(?=,|$)", set_part):
            col, expr = lit[0], lit[1].strip()
            if expr == "0":
                row[col] = 0
            elif expr == "NULL":
                row[col] = None
            elif expr.startswith("NOW()"):
                row[col] = "2026-11-04 00:00:00"
        if "version=version+1" in set_part:
            row["version"] = int(row["version"]) + 1
        if "published_at=COALESCE" in set_part and not row.get("published_at"):
            row["published_at"] = "2026-11-04 00:00:00"
        if "archived_at=NOW()" in set_part:
            row["archived_at"] = "2026-11-04 00:00:00"
        return 1


def _new_article(**over):
    base = {"id": 1, "slug": "scan-accuracy", "title": "口内扫描精度怎么衡量", "summary": "指标口径",
            "cover_object_key": "content/1/cover.png", "category_id": 7,
            "tags": json.dumps(["扫描精度"], ensure_ascii=False), "kind": "markdown",
            "link_url": "", "link_label": "", "refs": json.dumps(
                [{"title": "ISO 20896-1:2019", "url": ""}], ensure_ascii=False),
            "pinned": 0, "featured": 0, "source_type": "manual", "source_ref": "",
            "head_revision_id": 0, "live_revision_id": 0, "status": "draft",
            "publish_at": None, "published_at": None, "archived_at": None, "review_by": None,
            "version": 1, "view_count": 0, "created_by": "author", "updated_by": "author",
            "versioning": "keep_history",
            "created_at": None, "updated_at": None, "category_label": "技术白皮书",
            "last_archived_revision_id": 0}
    base.update(over)
    return base


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(svc, "execute_query", fake.query)
    monkeypatch.setattr(svc, "execute_write", fake.write)
    monkeypatch.setattr(svc, "execute_insert", fake.insert)
    return fake


def _seed(db, *, status="draft", live=0, body="拼接误差用 RMSD 表征，指标口径见正文。", author="author",
          versioning="keep_history"):
    """建一条带 head revision 的条目，返回 (article_id, revision_id)。"""
    aid = db.insert("INSERT INTO adh_content_articles (slug) VALUES (%s)",
                    ("scan-accuracy", "t", "s", 7, json.dumps(["扫描精度"]), "markdown",
                     "", "", json.dumps([{"title": "ISO 20896-1", "url": ""}]), 0, 0, status,
                     None, "manual", "", versioning, author, author))
    rid = db.insert("INSERT INTO adh_content_revisions (article_id, body) VALUES (%s,%s)",
                    (aid, body, "初稿", "h", author))
    db.articles[aid]["head_revision_id"] = rid
    db.articles[aid]["live_revision_id"] = live
    return aid, rid


# ── 1. 提交门禁 ───────────────────────────────────────────────────────────────

def test_gate_blocks_unfilled_placeholder(db):
    """占位未清零是硬阻断：填不出来就删段，不许带着【待填】发布。"""
    aid, _ = _seed(db, body="我们的返修率为【待填】%，数据来自内部统计。")
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert exc.value.status == 422
    assert "占位" in exc.value.detail


def test_gate_blocks_internal_identifiers(db):
    """正文含内部标识（连接串/生成 SQL/报错栈）硬阻断 —— 复用出站守卫同一份清单。"""
    aid, _ = _seed(db, body="连接串 jdbc:mysql://10.0.0.1:3306/adh2 里查 select a from db.tbl")
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert "内部标识" in exc.value.detail


def test_gate_blocks_dangling_category(db):
    aid, _ = _seed(db)
    db.articles[aid]["category_id"] = 999
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert "栏目" in exc.value.detail


def test_gate_blocks_link_without_url(db):
    aid, _ = _seed(db)
    db.articles[aid].update(kind="link", link_url="")
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert "原文地址" in exc.value.detail


# ── 1b. HTML 版式形态 ────────────────────────────────────────────────────────────────

def test_gate_accepts_html_body_with_layout_markup(db):
    """HTML 版式是正式形态之一：排版标记与 style 里的主题变量不该被门禁误拦。"""
    body = ('<section style="display:grid;gap:30px"><h3>企业发展时间线</h3>'
            '<span style="background:hsl(var(--primary))"></span>'
            '<a href="https://www.allied-star.com">官方网站</a></section>')
    aid, _ = _seed(db, body=body)
    db.articles[aid]["kind"] = svc.KIND_HTML
    hard, _warns = svc.gate_article(db.articles[aid], body)
    assert not hard


def test_gate_blocks_executable_markup_in_html_body(db):
    """脚本/事件属性/无作用域 style 块硬阻断：渲染侧本来就会剥掉它们，
    但“存得进去、渲染出来缺块东西”比报错难查，所以入库时就拒（no-silent-degradation）。"""
    for body in ('<p onclick="x()">正文</p>', "<script>alert(1)</script>",
                 "<style>body{display:none}</style>", '<a href="javascript:x()">链接</a>',
                 '<iframe src="https://evil.example"></iframe>'):
        aid, _ = _seed(db, body=body)
        db.articles[aid]["kind"] = svc.KIND_HTML
        with pytest.raises(svc.ContentError) as exc:
            svc.submit_review(aid, "author")
        assert exc.value.status == 422
        assert "不可用标记" in exc.value.detail


def test_html_body_without_markup_warns_not_blocks(db):
    """选了 HTML 却没写 HTML → 软提醒（该改回站内正文），不替人下结论。"""
    body = "一段纯文字，没有任何排版标记。"
    aid, _ = _seed(db, body=body)
    db.articles[aid]["kind"] = svc.KIND_HTML
    hard, warns = svc.gate_article(db.articles[aid], body)
    assert not hard
    assert "html_no_tags" in {w["code"] for w in warns}


def test_html_kind_is_open_and_labeled(db):
    """形态名单与 label 同源：写入口收 html，未知形态仍显式拒。"""
    assert svc._validate_kind("html") == svc.KIND_HTML
    assert svc.KIND_LABELS[svc.KIND_HTML] == "HTML 版式"
    with pytest.raises(svc.ContentError):
        svc._validate_kind("rich")


def test_gate_warns_missing_refs_but_does_not_block(db):
    """出处缺失是软警告：可以提交，但必须被审核人逐条确认（不是静默放行）。"""
    aid, _ = _seed(db)
    db.articles[aid]["refs"] = "[]"
    out = svc.submit_review(aid, "author")
    assert out["warnings"] and out["warnings"][0]["code"] == "refs_missing"
    assert "提醒" in out["message"]


def test_gate_warns_pii_and_small_sample(db):
    """疑似可识别形态与小样本只提醒不自动拒 —— 裁决权在人（护栏 §7 同源纪律）。"""
    aid, _ = _seed(db, body="患者张三的就诊号 20260012，本次 8 例观察显示贴合度改善。")
    _hard, warns = svc.gate_article(db.articles[aid], svc._revision_body(db.articles[aid]["head_revision_id"]))
    codes = {w["code"] for w in warns}
    assert {"pii_hint", "small_sample"} <= codes
    assert all(w["line"] > 0 for w in warns if w["code"] in {"pii_hint", "small_sample"})


def test_gate_warns_expired_review_date(db):
    from datetime import date, timedelta
    aid, _ = _seed(db)
    db.articles[aid]["review_by"] = date.today() - timedelta(days=1)
    _hard, warns = svc.gate_article(db.articles[aid], "正文")
    assert "review_expired" in {w["code"] for w in warns}


# ── 2. 状态机 ─────────────────────────────────────────────────────────────────

def test_publish_requires_approval(db):
    """未审核不得发布（分级审核的默认那一级）。"""
    aid, _ = _seed(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.publish_article(aid, "boss")
    assert "免审直发" in exc.value.detail


def test_waive_publish_requires_reason(db):
    aid, _ = _seed(db, status="draft")
    svc.submit_review(aid, "author")
    db.articles[aid]["status"] = "draft"
    with pytest.raises(svc.ContentError) as exc:
        svc.publish_article(aid, "boss", waive_review=True, reason="")
    assert "原因" in exc.value.detail


def test_happy_path_draft_to_published(db):
    aid, rid = _seed(db)
    assert svc.submit_review(aid, "author")["status"] == "in_review"
    ack = [{"code": w["code"], "acknowledged": True} for w in svc.pending_reviews()["items"][0]["gate_warnings"]]
    assert svc.approve_review(aid, "reviewer", comment="口径核对过", acknowledged=ack)["status"] == "approved"
    out = svc.publish_article(aid, "publisher")
    assert out["status"] == "published"
    assert db.articles[aid]["live_revision_id"] == rid
    # 线上版本没有新改动时再次发布应被拒（避免"重复发布"被当成幂等成功而掩盖误操作）
    with pytest.raises(svc.ContentError):
        svc.publish_article(aid, "publisher", expected_version=db.articles[aid]["version"])


def test_reject_needs_reason_and_returns_to_draft(db):
    aid, _ = _seed(db)
    svc.submit_review(aid, "author")
    with pytest.raises(svc.ContentError) as exc:
        svc.reject_review(aid, "reviewer", comment="")
    assert "原因" in exc.value.detail
    assert svc.reject_review(aid, "reviewer", comment="样本量说明缺失")["status"] == "draft"


def test_approve_blocks_until_warnings_acknowledged(db):
    """软警告没勾完不能点通过 —— 否则审核就成了橡皮章。"""
    aid, _ = _seed(db)
    db.articles[aid]["refs"] = "[]"
    svc.submit_review(aid, "author")
    with pytest.raises(svc.ContentError) as exc:
        svc.approve_review(aid, "reviewer")
    assert exc.value.status == 409
    assert "确认" in exc.value.detail


def test_author_cannot_review_own_revision(db):
    """自审拦截（非 admin）：审核人不得是这一版的作者。"""
    aid, rid = _seed(db, author="alice")
    svc.submit_review(aid, "alice")
    with pytest.raises(svc.ContentError) as exc:
        svc.approve_review(aid, "alice")
    assert exc.value.status == 403
    assert "自己" in exc.value.detail
    # 拒了就不能顺手把状态推过去（否则下次提交变成“已审”假象）
    assert db.articles[aid]["status"] == "in_review"


def test_admin_may_approve_own_revision_but_must_be_told(db):
    """admin 可审自己写的稿（拍板），但职责分离的例外不得隐身。

    锁两件事：① 回执带 self_approved + 可读提醒（前端必须显示，不能只取 message）；
    ② 审核流水仍按真实操作人记账（事后追溯能看出作者与审核人同一）。
    为什么可以：admin 本来就能勾「免审直发」（只需填原因），拦住建他只造死胡同。
    """
    aid, _ = _seed(db, author="admin")
    svc.submit_review(aid, "admin")
    ack = [{"code": w["code"], "acknowledged": True}
           for w in svc.pending_reviews()["items"][0]["gate_warnings"]]
    out = svc.approve_review(aid, "admin", acknowledged=ack, is_admin=True)
    assert out["status"] == "approved"
    assert out.get("self_approved") and "作者本人" in out["warning"], out
    assert db.articles[aid]["status"] == "approved"
    assert db.reviews[-1]["actor"] == "admin"


def test_self_review_flag_defaults_off_for_non_admin_callers(db):
    """`is_admin` 默认 False：调用方漏传只会回到严格口径，不会把自审放行当默认。"""
    aid, _ = _seed(db, author="alice")
    svc.submit_review(aid, "alice")
    with pytest.raises(svc.ContentError):
        svc.approve_review(aid, "alice", is_admin=False)


def test_cannot_submit_twice_while_in_review(db):
    aid, _ = _seed(db)
    svc.submit_review(aid, "author")
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert "审核中" in exc.value.detail


def test_archive_clears_live(db):
    aid, _ = _seed(db, live=0)
    svc.submit_review(aid, "author")
    ack = [{"code": w["code"], "acknowledged": True} for w in svc.pending_reviews()["items"][0]["gate_warnings"]]
    svc.approve_review(aid, "reviewer", acknowledged=ack)
    svc.publish_article(aid, "publisher")
    out = svc.archive_article(aid, "publisher", reason="口径待更新")
    assert out["status"] == "archived"
    assert db.articles[aid]["live_revision_id"] == 0
    # 从未同步过的稿子没有语料要撤，不得虚警“语料还在库里”（虚警多了就没人看真提醒）
    assert "warning" not in out, out


def test_archive_warns_when_corpus_is_still_in_kb(db):
    """已同步过的内容下架后语料还在库里 —— 必须说出来，AS-BOT 仍会拿它回答用户。"""
    aid, _ = _seed(db, status="published", live=500)
    db.kb_sync.append({"article_id": aid, "kb_name": "云端库", "doc_title": "a.md",
                       "state": "synced", "revision_id": 1})
    out = svc.archive_article(aid, "publisher", reason="口径待更新")
    assert "云端库" in out["warning"] and "a.md" in out["warning"], out
    assert "知识库文档" in out["warning"], "要指回去哪处理，不是只说有问题"


def test_archive_failure_attempt_does_not_claim_corpus_left(db):
    """上次同步就失败了 → 库里根本没有那篇，不得误报“语料还在”。"""
    aid, _ = _seed(db, status="published", live=500)
    db.kb_sync.append({"article_id": aid, "kb_name": "云端库", "doc_title": "a.md",
                       "state": "failed", "revision_id": 1})
    assert "warning" not in svc.archive_article(aid, "publisher", reason="x")


def _publish_then_archive(db, aid, *, actor="publisher"):
    """走完审核→发布→下架，返回当时上线那一版（重新上架要放回去的就是它）。"""
    svc.submit_review(aid, "author")
    ack = [{"code": w["code"], "acknowledged": True} for w in svc.pending_reviews()["items"][0]["gate_warnings"]]
    svc.approve_review(aid, "reviewer", acknowledged=ack)
    svc.publish_article(aid, actor)
    live = db.articles[aid]["live_revision_id"]
    svc.archive_article(aid, actor, reason="口径待更新")
    return live


def test_republish_restores_the_archived_version(db):
    """已下架必须能重新上架：放回去的是下架时在线那一版，那一版当初审过 → 不重新走审核。"""
    aid, rid = _seed(db)
    live = _publish_then_archive(db, aid)
    assert db.articles[aid]["live_revision_id"] == 0

    out = svc.republish_article(aid, "publisher", reason="已复核，口径不变")
    assert out["status"] == "published"
    assert db.articles[aid]["live_revision_id"] == live == rid
    assert db.articles[aid]["archived_at"] is None, "重新上架后不得还挂着下架时间"
    assert "重新上架" in out["message"]
    assert "warning" not in out, out
    # 流水必须可追溯：action=republish 且针对被放回的那一版
    assert db.reviews[-1]["action"] == "republish" and db.reviews[-1]["revision_id"] == live


def test_republish_does_not_release_edits_made_after_archive(db):
    """关键护栏：下架后又改的正文不得跟着上架（那一版没审过），新改动留在 head。"""
    aid, _ = _seed(db)
    live = _publish_then_archive(db, aid)
    svc.update_article(aid, {"body": "下架之后改的新版"}, "author",
                       expected_version=db.articles[aid]["version"])
    head = db.articles[aid]["head_revision_id"]
    assert head != live

    out = svc.republish_article(aid, "publisher")
    assert db.articles[aid]["live_revision_id"] == live, "放回的一定是下架前那一版"
    assert db.articles[aid]["head_revision_id"] == head, "新改动不得被抹掉"
    assert out["status"] == "draft", "head 是没审过的新版 → 工作流态回到草稿"
    assert "没跟着上线" in out["warning"], "改动没上线必须显式说出来，不能当成全部上线"
    # 门户读到的仍是旧版正文（与新 head 隔开）
    assert svc.get_article(article_id=aid, can_edit=False)["body"] == "拼接误差用 RMSD 表征，指标口径见正文。"


def test_republish_without_archive_trace_fails_loud(db):
    """追溯不到下架那一版（无 archive 流水）不得回退成“那就发 head”。"""
    aid, _ = _seed(db, status="archived", live=0)
    with pytest.raises(svc.ContentError) as exc:
        svc.republish_article(aid, "publisher")
    assert exc.value.status == 409
    assert "提交审核" in exc.value.detail
    assert db.articles[aid]["live_revision_id"] == 0, "拒了就必须什么都没上线"


def test_republish_rejects_non_archived_and_stale_version(db):
    aid, _ = _seed(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.republish_article(aid, "publisher")
    assert "已下架" in exc.value.detail

    _publish_then_archive(db, aid)
    with pytest.raises(svc.ContentError) as exc:
        svc.republish_article(aid, "publisher", expected_version=99)
    assert exc.value.status == 409


def test_publish_on_archived_item_points_to_republish(db):
    """拿发布动作碰已下架条目：拒得对，并把“该走哪条”说清楚（不是只说没审核）。"""
    aid, _ = _seed(db, status="archived", live=0)
    with pytest.raises(svc.ContentError) as exc:
        svc.publish_article(aid, "publisher")
    assert "重新上架" in exc.value.detail


def test_republish_flags_are_computed_by_server(db):
    """上架/送审入口靠服务端两个布尔驱动，前端拿不到 revision 也不自己推。"""
    aid, _ = _seed(db)
    item = svc.get_article(article_id=aid, can_edit=True, revision="head")
    assert item["can_republish"] is False and item["head_changed_since_archive"] is False

    _publish_then_archive(db, aid)
    item = svc.get_article(article_id=aid, can_edit=True, revision="head")
    assert item["can_republish"] is True and item["head_changed_since_archive"] is False

    svc.update_article(aid, {"body": "下架后又改"}, "author",
                       expected_version=db.articles[aid]["version"])
    item = svc.get_article(article_id=aid, can_edit=True, revision="head")
    assert item["can_republish"] is True, "旧版仍可放回"
    assert item["head_changed_since_archive"] is True, "新改动得走审核"
    assert "last_archived_revision_id" not in item, "revision 属内部标识不下发"


def test_published_article_cannot_be_deleted(db):
    """已上线只能下架不能删（删了审核流水就断了追溯链）。"""
    aid, _ = _seed(db)
    db.articles[aid]["live_revision_id"] = 500
    with pytest.raises(svc.ContentError) as exc:
        svc.delete_article(aid, "author")
    assert "下架" in exc.value.detail


# ── 3. 版本模型与并发 ─────────────────────────────────────────────────────────

def test_deleting_draft_also_clears_review_rows(db):
    """删条目必须连审核流水一起清。

    这几张关系表没建外键，清理全靠 delete_article 里的显式 DELETE；漏一张就会
    留下指向不存在条目的孤儿行（真库实测过：5 行已删条目的 submit 流水按 article_id
    聚合时会被当成“另有 5 篇没走完流程的内容”）。已上线条目不允许直接删，
    所以这里不存在“把上线审计抹掉”的风险。
    """
    aid, _ = _seed(db)
    svc.submit_review(aid, "author")
    assert any(int(r["article_id"]) == aid for r in db.reviews)
    svc.delete_article(aid, "author")
    assert aid not in db.articles
    assert [r for r in db.reviews if int(r["article_id"]) == aid] == []


def test_editing_published_content_does_not_touch_live(db):
    """关键护栏：编辑已发布内容只产生新 head，线上继续展示 live 版本。"""
    aid, rid = _seed(db)
    svc.submit_review(aid, "author")
    ack = [{"code": w["code"], "acknowledged": True} for w in svc.pending_reviews()["items"][0]["gate_warnings"]]
    svc.approve_review(aid, "reviewer", acknowledged=ack)
    svc.publish_article(aid, "publisher")
    live_before = db.articles[aid]["live_revision_id"]

    svc.update_article(aid, {"body": "改了一半的新稿"}, "author",
                       expected_version=db.articles[aid]["version"])
    assert db.articles[aid]["live_revision_id"] == live_before     # 线上指针没动
    assert db.articles[aid]["status"] == "draft"                   # head 回到待审
    assert svc._revision_body(db.articles[aid]["live_revision_id"]) != "改了一半的新稿"
    # 门户读到的仍是旧版本正文
    assert svc.get_article(article_id=aid, can_edit=False)["body"] == "拼接误差用 RMSD 表征，指标口径见正文。"


def test_optimistic_lock_rejects_stale_version(db):
    aid, _ = _seed(db)
    v_before = db.articles[aid]["version"]
    with pytest.raises(svc.ContentError) as exc:
        svc.update_article(aid, {"summary": "新摘要"}, "other", expected_version=99)
    assert exc.value.status == 409
    assert "刷新" in exc.value.detail
    # 拒了就必须什么都没改（半写会让"刷新后重试"失去意义）
    assert db.articles[aid]["version"] == v_before
    assert db.articles[aid]["summary"] != "新摘要"


def test_edit_while_in_review_is_refused(db):
    aid, _ = _seed(db)
    svc.submit_review(aid, "author")
    before = set(db.revisions)
    with pytest.raises(svc.ContentError) as exc:
        svc.update_article(aid, {"body": "偷偷改正文"}, "author")
    assert "审核中" in exc.value.detail
    # 拒得必须在副作用之前：留一行没人引用的 revision 就是往版本清单里塞假历史
    assert set(db.revisions) == before


# ── 3b. 期次（已发布版本）与版本策略 ────────────────────────────────────


def _publish(db, aid, actor="pub", **kw):
    """免审直发一期（门禁已满足，期次语义与审核链路正交）。"""
    return svc.publish_article(aid, actor, waive_review=True, reason="首批发布", **kw)


def _next_issue(db, aid, body, actor="author"):
    """改正文 → 新一期（已发布内容改动只进 head，需重新审核才能发）。"""
    svc.update_article(aid, {"body": body}, actor, expected_version=db.articles[aid]["version"])
    svc.submit_review(aid, actor)
    ack = [{"code": w["code"], "acknowledged": True}
           for w in svc.pending_reviews()["items"][0]["gate_warnings"]]
    svc.approve_review(aid, "reviewer", comment="新一期口径核对", acknowledged=ack)
    return svc.publish_article(aid, "pub")


def test_publish_allocates_monotonic_release_numbers(db):
    """发布就是开一期：期次号本篇内递增，不重号、不漏号。"""
    aid, _ = _seed(db)
    first = _publish(db, aid)
    assert (first["release"], first["release_count"]) == (1, 1)
    second = _next_issue(db, aid, "新一期：本月装机量口径已更新。")
    assert (second["release"], second["release_count"]) == (2, 2)
    assert "第 2 期" in second["message"]


def test_release_label_comes_from_publisher_not_change_note(db):
    """期次名是发布人给的业务名；缺省自动按当月，不拿 change_note 充数。"""
    aid, _ = _seed(db)
    out = _publish(db, aid, release_label="2026年11月行业月报")
    assert out["release_label"] == "2026年11月行业月报"
    listed = svc.releases_of(aid, for_reader=False)["items"]
    assert listed[0]["label"] == "2026年11月行业月报"
    aid2, _ = _seed(db)
    auto = _publish(db, aid2)
    assert auto["release_label"] and auto["release_label"] != "初稿"


def test_republishing_same_revision_does_not_renumber(db):
    """已发布过的版本不重新编号（否则“第 N 期”会跟审核流水里的版本对不上）。"""
    aid, _ = _seed(db)
    assert _publish(db, aid)["release"] == 1
    # 回到待发布态后重发同一版：期次号仍是 1，不出现“第 2 期 = 同一份正文”
    db.articles[aid]["status"] = "approved"
    out = svc.publish_article(aid, "pub", waive_review=True, reason="重发同一版")
    assert out["release"] == 1
    assert svc._release_count(aid) == 1


def test_latest_only_hides_history_from_readers_but_not_from_editors(db):
    aid, _ = _seed(db, versioning=svc.VERSIONING_LATEST)
    _publish(db, aid)
    _next_issue(db, aid, "新一期正文。")
    reader = svc.releases_of(aid, for_reader=True)
    assert reader["history_hidden"] is True and len(reader["items"]) == 1
    assert reader["items"][0]["is_current"] is True
    editor = svc.releases_of(aid, for_reader=False)
    assert editor["history_hidden"] is False and len(editor["items"]) == 2
    # 读者直读旧期也被拒（与清单同一口径，不能“清单里没有但直链能读到”）
    with pytest.raises(svc.ContentError) as exc:
        svc.get_article(article_id=aid, can_edit=False, release=1)
    assert exc.value.status == 404 and "只发布最新版本" in exc.value.detail
    # 编排面不受策略限制（回滚与对比旧期需要）
    assert svc.get_article(article_id=aid, can_edit=True, release=1)["viewing_release"] == 1


def test_switching_policy_back_to_keep_history_restores_visibility(db):
    """策略只收展示面、不删数据：改回 keep_history 后历史期次无需重发就恢复可见。"""
    aid, _ = _seed(db, versioning=svc.VERSIONING_LATEST)
    _publish(db, aid)
    _next_issue(db, aid, "新一期正文。")
    assert len(svc.releases_of(aid, for_reader=True)["items"]) == 1
    svc.update_article(aid, {"versioning": svc.VERSIONING_KEEP}, "editor",
                       expected_version=db.articles[aid]["version"])
    reader = svc.releases_of(aid, for_reader=True)
    assert reader["history_hidden"] is False and len(reader["items"]) == 2
    assert svc.get_article(article_id=aid, can_edit=False, release=1)["viewing_release"] == 1


def test_unknown_release_number_is_refused_with_candidates(db):
    aid, _ = _seed(db)
    _publish(db, aid)
    with pytest.raises(svc.ContentError) as exc:
        svc.get_article(article_id=aid, can_edit=True, release=9)
    assert exc.value.status == 404 and "第 9 期" in exc.value.detail and "1" in exc.value.detail


def test_release_and_head_revision_params_are_mutually_exclusive(db):
    """双口径参数同时给不得“静默优先其中一个”（那会把前端写错的调用伪装成能跑）。"""
    aid, _ = _seed(db)
    _publish(db, aid)
    with pytest.raises(svc.ContentError) as exc:
        svc.get_article(article_id=aid, can_edit=True, revision="head", release=1)
    assert "不能同时给" in exc.value.detail


def test_article_output_carries_release_verdict_without_revision_ids(db):
    aid, _ = _seed(db)
    _publish(db, aid)
    _next_issue(db, aid, "新一期正文。")
    out = svc.get_article(article_id=aid, can_edit=True)
    assert out["release"] == 2 and out["release_count"] == 2 and out["can_rollback"] is True
    assert out["viewing_release"] == 2 and out["viewing_is_current"] is True
    # 内部标识不外泄：期次只能拿号访问，revision 指针不得出现在响应体（护栏 §7）
    assert "live_revision_id" not in out and "head_revision_id" not in out


def test_can_rollback_false_until_two_releases_exist(db):
    """只有一个发布版本时不给“切换展示版本”：那是个只能选中自己的死开关。"""
    aid, _ = _seed(db)
    assert svc.get_article(article_id=aid, can_edit=True)["can_rollback"] is False
    _publish(db, aid)
    assert svc.get_article(article_id=aid, can_edit=True)["can_rollback"] is False
    _next_issue(db, aid, "新一期正文。")
    assert svc.get_article(article_id=aid, can_edit=True)["can_rollback"] is True


def test_rollback_switches_live_without_touching_head_or_body(db):
    """回滚 = live 指回旧期：不重写正文，也不把没审过的 head 顺带放出去。"""
    aid, rid1 = _seed(db, body="第一期正文。")
    _publish(db, aid)
    _next_issue(db, aid, "第二期正文。")
    rid2 = db.articles[aid]["head_revision_id"]
    out = svc.rollback_to_release(aid, "pub", 1)
    assert out["release"] == 1 and db.articles[aid]["live_revision_id"] == rid1
    assert db.articles[aid]["head_revision_id"] == rid2, "草稿位没被动过"
    assert out["status"] == "draft" and "没跟着上线" in out["warning"]
    assert svc._release_count(aid) == 2, "切换展示版本不得产生新一期"
    assert svc.get_article(article_id=aid, can_edit=False)["body"] == "第一期正文。"
    # 流水能分辨这件事：action=rollback
    assert {r["action"] for r in svc.review_history(aid)} >= {"rollback"}


def test_rollback_refuses_current_unknown_and_offline(db):
    aid, _ = _seed(db)
    _publish(db, aid)
    _next_issue(db, aid, "第二期正文。")
    with pytest.raises(svc.ContentError) as exc:
        svc.rollback_to_release(aid, "pub", 2)
    assert exc.value.status == 409 and "已经在线上" in exc.value.detail
    with pytest.raises(svc.ContentError) as exc:
        svc.rollback_to_release(aid, "pub", 7)
    assert "没有第 7 期" in exc.value.detail
    svc.archive_article(aid, "pub", reason="先下架")
    with pytest.raises(svc.ContentError) as exc:
        svc.rollback_to_release(aid, "pub", 1)
    assert "没有在线版本" in exc.value.detail


def test_versioning_value_is_validated(db):
    aid, _ = _seed(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.update_article(aid, {"versioning": "keep_all"}, "editor",
                           expected_version=db.articles[aid]["version"])
    assert "版本策略" in exc.value.detail


def test_publish_message_states_history_policy_consequence(db):
    """只保留最新时，上一期不再对读者展示这件事必须在回执里说出来（不能只改库不提醒）。"""
    aid, _ = _seed(db)
    _publish(db, aid, release_label="10 月期")
    db.articles[aid]["versioning"] = svc.VERSIONING_LATEST
    out = _next_issue(db, aid, "新一期正文。")
    assert "只保留最新版本" in out["message"] and "10 月期" in out["message"]


def test_rollback_pointer_is_just_live_retarget(db):
    """回滚不需要重写正文：live 指回历史 revision 即可（版本清单是回滚数据源）。"""
    aid, rid1 = _seed(db)
    svc.update_article(aid, {"body": "第二版"}, "author", expected_version=db.articles[aid]["version"])
    rid2 = db.articles[aid]["head_revision_id"]
    assert rid2 != rid1
    assert {r["id"] for r in svc.revision_list(aid)} == {rid1, rid2}


# ── 4. 写入口约束 ─────────────────────────────────────────────────────────────

def test_retired_promoted_kind_is_rejected_with_direction(db):
    """提升形态仍拒，但去向说清：报告提升现在落地为正文版本，不再是“后续批次”空话。

    附件（kind=file）已在 P1 随上传通道开放，不再属"未开放"，所以断言对象换成 promoted：
    保留 file 会让这条测试与 OPEN_KINDS 的真实口径相互打脸。
    """
    assert "file" in svc.OPEN_KINDS and "promoted" not in svc.OPEN_KINDS
    assert svc.KIND_PROMOTED not in svc.KIND_LABELS, "提升形态已退役，不得再当可选形态"
    assert svc._kind_label(svc.KIND_PROMOTED) == "平台内提升（历史形态）", "存量行也要给人话读法"
    with pytest.raises(svc.ContentError) as exc:
        svc.create_article({"title": "提升的报告", "category": "whitepaper", "kind": "promoted"}, "author")
    detail = exc.value.detail
    assert "已退役" in detail and "不依赖报表快照" in detail


def test_tags_must_come_from_controlled_vocab(db):
    """标签只能从受控词表选 —— 自由输入会把标签云写成孤儿堆。"""
    with pytest.raises(svc.ContentError) as exc:
        svc.create_article({"title": "随便写", "category": "whitepaper", "tags": ["我造的标签"]}, "author")
    assert "受控词表" in exc.value.detail


def test_dangling_category_rejected_on_create(db):
    with pytest.raises(svc.ContentError) as exc:
        svc.create_article({"title": "随便写", "category": "不存在的栏目"}, "author")
    assert "现有栏目" in exc.value.detail       # 回候选名，引导用户改选而不是猜


def test_category_with_articles_cannot_be_deleted(db):
    aid, _ = _seed(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.delete_category(7, "admin")
    assert "还有" in exc.value.detail


# ── 5. 响应体与可见性 ─────────────────────────────────────────────────────────

def test_response_has_no_internal_identifiers(db):
    """内部标识不外泄：对象存储 key、提升溯源、revision 指针都不进响应体（护栏 §7）。"""
    aid, _ = _seed(db)
    out = svc.get_article(article_id=aid, can_edit=True, revision="head")
    for key in ("cover_object_key", "source_ref", "head_revision_id", "live_revision_id"):
        assert key not in out, key
    assert out["is_live"] is False
    assert out["category_label"] == "技术白皮书"      # 显示用 name，不显示裸 id


def test_draft_is_invisible_to_reader(db):
    """草稿对普通读者不可见：服务端就一道门，不靠前端不显示。"""
    aid, _ = _seed(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.get_article(article_id=aid, can_edit=False)
    assert exc.value.status == 404
    assert svc.get_article(article_id=aid, can_edit=True, revision="head")["status"] == "draft"


def test_list_defaults_to_live_only(db):
    aid, _ = _seed(db)
    assert svc.list_articles()["total"] == 0            # 未发布不进门户清单
    # 草稿只在个人区：非 mine 筛 draft 是不合法组合（报错指路，不静默给空清单）
    with pytest.raises(svc.ContentError):
        svc.list_articles(status="draft", can_edit=True)
    assert svc.list_articles(status="draft", mine="author", can_edit=True)["total"] == 1
    assert svc.list_articles(status="draft", mine="author", can_edit=False)["total"] == 0


def test_list_exclude_category_yields_feed_to_profile_card(db):
    """首页资讯流把「关于本公司」让给简介卡：排除在服务端做，总数随同一口径减。

    两条易错面都钉住：未挂栏目的条目不得被 NOT(...) 连带筛掉（LEFT JOIN 下谓词为
    NULL 即静默少内容）；key/id 两种写法与 category 叠加时同一口径。
    """
    db.categories[9] = {"id": 9, "category_key": "about", "label": "关于本公司", "is_active": 1}
    keep, keep_rid = _seed(db, status="published")
    db.articles[keep]["live_revision_id"] = keep_rid
    about, about_rid = _seed(db, status="published")
    db.articles[about]["live_revision_id"] = about_rid
    db.articles[about]["category_id"] = 9
    no_cat, no_cat_rid = _seed(db, status="published")
    db.articles[no_cat]["live_revision_id"] = no_cat_rid
    db.articles[no_cat]["category_id"] = 0

    out = svc.list_articles(exclude_category="about")
    assert {a["id"] for a in out["items"]} == {keep, no_cat} and out["total"] == 2, out
    # id 口径与 key 口径同一结果
    assert svc.list_articles(exclude_category="9")["total"] == 2
    # 与 category 叠加（点其它栏目芯片时让位条件照常生效）
    assert svc.list_articles(category="7", exclude_category="about")["total"] == 1
    # 不传 = 行为零变化（编排面/简介卡旧调用方不受影响）
    assert svc.list_articles()["total"] == 3


def test_list_query_uses_live_predicate(db, monkeypatch):
    """门户可见性判据只有一处（LIVE_WHERE），各处不得各写一套。"""
    seen = []
    orig = svc.execute_query

    def spy(sql, params=None, **kw):
        seen.append(sql)
        return orig(sql, params, **kw)
    monkeypatch.setattr(svc, "execute_query", spy)
    svc.list_articles()
    assert any(svc.LIVE_WHERE in s for s in seen)


def test_list_all_status_without_filters_is_valid_sql(db, monkeypatch):
    """可编辑者选「全部状态」且无其他筛选：拼出的 WHERE 必须带条件，不得是 "WHERE ORDER BY"。

    实机缺陷（用户 2026-11-04 截图）：内容管理页 GET /api/content/articles 直接 500，
    MySQL 1064 near '' —— 拼 SQL 时直接 join 空列表会生成 "WHERE ORDER BY"。
    2026-11-13 起编排面恒定排除草稿，where 更不可能为空；这里同时钉住那一条。
    """
    _seed(db)                                    # 草稿：不进取数编排面
    live, rid = _seed(db, status="published")
    db.articles[live]["live_revision_id"] = rid
    seen = []
    orig = svc.execute_query

    def spy(sql, params=None, **kw):
        seen.append(sql)
        return orig(sql, params, **kw)
    monkeypatch.setattr(svc, "execute_query", spy)
    out = svc.list_articles(can_edit=True, status="all", page=1, page_size=20, sort="latest")
    assert out["total"] == 1 and len(out["items"]) == 1, out
    assert seen, "必须真发过 SQL（不得被提前短路）"
    assert "a.status<>%s" in seen[0], seen[0]      # 排草稿的谓词由服务端加，不是前端不显示
    for sql in seen:
        assert not re.search(r"WHERE\s*(?:ORDER|LIMIT|OFFSET|$)", sql), sql


# ── 6. slug 与身份 ────────────────────────────────────────────────────────────

def test_slugify_keeps_business_readable_name():
    assert svc.slugify("口内扫描精度怎么衡量：从单帧误差到全弓拼接误差") == "口内扫描精度怎么衡量-从单帧误差到全弓拼接误差"
    assert svc.slugify("!!!") == "article"


def test_duplicate_slug_gets_suffix(db):
    _seed(db)
    assert svc._unique_slug("scan-accuracy") == "scan-accuracy-2"


def test_viewer_can_edit_is_fail_closed(monkeypatch):
    """拿不到权限就当不可见；空角色授权不得解释为全量。"""
    monkeypatch.setattr(svc, "EDIT_PERM", "article:manage")
    assert svc.viewer_can_edit({}) is False
    assert svc.viewer_can_edit({"user_id": 0}) is False
    assert svc.viewer_can_edit({"user_id": 3, "role": "admin"}) is True    # admin 口径与中间件一致

    def boom(*a, **k):
        raise RuntimeError("权限服务不可用")
    from backend.core import role_service
    monkeypatch.setattr(role_service.role_service, "get_user_role_perm_codes", boom)
    assert svc.viewer_can_edit({"user_id": 3, "role": "analyst"}) is False


# ── 5. 迁移登记与端点契约（离线解析 SQL, 不连库）──────────────────────────────
#
# 为什么值得单独锁: 内容中心的权限裁决点**只有** api_pattern × api_method 这一处,
# handler 里没有第二道判断。迁移里把 pattern 写错一个字符, 表现是"角色勾了权限仍 403"
# 或"只勾审核权却能发布" —— 两类都静默, 只能靠断言拦住。
from backend.common.api_permission import _match_path

MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docker", "mysql", "content_center_migration.sql")
RELEASE_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docker", "mysql", "content_release_migration.sql")


def _apply_release_patterns(perms: dict) -> dict:
    """把后续迁移 CONCAT 追加的 pattern 并回权限矩阵（与真库执行顺序同口径）。

    只看形状，不连库：没那条 UPDATE 就当没追加（新端点就会测成“零命中=403”）。
    追加必须同时带幂等守卫，否则重跑一次迁移 pattern 里就多一份 —— 那仍算放行，
    所以守卫缺失在这里也要能发现（不静默兼容“能跑但不幂等”的迁移）。
    """
    with open(RELEASE_MIGRATION, encoding="utf-8") as f:
        sql = f.read()
    for m in re.finditer(r"UPDATE\s+adh_perm_registry\s+SET\s+api_pattern\s*=\s*CONCAT\(\s*api_pattern\s*,\s*'([^']*)'\s*\)"
                         r"\s+WHERE\s+perm_code\s*=\s*'([^']+)'([^;]*);", sql, re.S | re.I):
        added, code, guard = m.group(1), m.group(2), m.group(3)
        assert "NOT LIKE" in guard.upper(), f"{code} 的 pattern 追加不幂等（重跑会接重复值）"
        if code in perms:
            perms[code]["patterns"] += [p.strip() for p in added.strip(",").split(",") if p.strip()]
    return perms


def _read_migration() -> str:
    with open(MIGRATION, encoding="utf-8") as f:
        return f.read()


def _perm_rows(sql: str) -> dict:
    """从 INSERT INTO adh_perm_registry 块里抽出 article:* 的 (api_pattern, api_method)。"""
    body = sql.split("INSERT INTO adh_perm_registry", 1)[1].split("ON DUPLICATE KEY UPDATE", 1)[0]
    out = {}
    for seg in re.split(r"\n\s{2}\('", body):
        seg = "'" + seg if not seg.startswith("--") else seg
        m = re.match(r"'(article:[a-z]+)'", seg)
        if not m:
            continue
        pat = re.search(r"'(/api/content[^']*)'", seg)
        meth = re.search(r"'((?:GET|POST|PUT|PATCH|DELETE)(?:,(?:GET|POST|PUT|PATCH|DELETE))*)'", seg)
        menu = re.search(r"'(content:[a-z-]+)'", seg)
        ai = re.search(r"'(read|write|none)'", seg)
        out[m.group(1)] = {
            "patterns": [x.strip() for x in (pat.group(1) if pat else "").split(",") if x.strip()],
            "methods": [x.strip() for x in (meth.group(1) if meth else "").split(",") if x.strip()],
            "menu_key": menu.group(1) if menu else "",
            "ai_access": ai.group(1) if ai else "",
        }
    return out


def test_migration_registers_only_shipped_menus():
    """没页面/没端点的登记行 = 角色页能勾、侧栏没入口的死开关, 必须留在后续批次。"""
    sql = _read_migration()
    menu_block = sql.split("INSERT INTO adh_menu_registry", 1)[1].split("ON DUPLICATE KEY UPDATE", 1)[0]
    keys = set(re.findall(r"'(content:[a-z-]+)'", menu_block))
    assert keys == {"content:drafts", "content:manage", "content:review", "content:taxonomy"}, keys
    # 资讯门户与语料同步都还没开发, 不得提前登记
    for deferred in ("content:feed", "content:sync", "article:read", "article:sync"):
        assert deferred not in menu_block, deferred


def test_perm_pattern_matrix_matches_endpoints():
    perms = _apply_release_patterns(_perm_rows(_read_migration()))
    assert set(perms) == {"article:manage", "article:review", "article:publish"}, set(perms)

    def allowed(method, path):
        return {code for code, r in perms.items()
                if method in r["methods"] and any(_match_path(p, path) for p in r["patterns"])}

    # 每条端点必须**恰好**落在一个码上（多命中=越权, 零命中=勾了权限仍 403）
    cases = [
        ("GET", "/api/content/articles", {"article:manage"}),
        ("GET", "/api/content/articles/a1", {"article:manage"}),
        ("GET", "/api/content/categories", {"article:manage"}),
        ("POST", "/api/content/articles", {"article:manage"}),
        ("PUT", "/api/content/articles/a1", {"article:manage"}),
        ("POST", "/api/content/articles/a1/submit", {"article:manage"}),
        # 批量端点不新增权限码：逐条走单条实现，所以必须恰好落在单条已登记的 pattern 上
        # （多命中=越权：只勾编辑权就能发布；零命中=空承诺：勾了权限照样 403）
        ("POST", "/api/content/articles/batch-submit", {"article:manage"}),
        ("POST", "/api/content/articles/batch-delete", {"article:manage"}),
        ("POST", "/api/content/publish-batch", {"article:publish"}),
        # 附件全量落在已有 pattern 内（不新增权限码）: 编排面走 manage, 审核面下载走 review
        ("GET", "/api/content/articles/a1/files", {"article:manage"}),
        ("POST", "/api/content/articles/a1/files", {"article:manage"}),
        ("DELETE", "/api/content/articles/a1/files/9", {"article:manage"}),
        ("GET", "/api/content/articles/a1/files/9/download", {"article:manage"}),
        ("GET", "/api/content/articles/a1/files/9/download-url", {"article:manage"}),
        ("GET", "/api/content/reviews/a1/files/9/download", {"article:review"}),
        ("GET", "/api/content/reviews/a1/files/9/download-url", {"article:review"}),
        ("GET", "/api/content/reviews/pending", {"article:review"}),
        ("GET", "/api/content/reviews/a1/draft", {"article:review"}),
        ("POST", "/api/content/reviews/a1/approve", {"article:review"}),
        ("POST", "/api/content/reviews/a1/reject", {"article:review"}),
        ("POST", "/api/content/publish/a1", {"article:publish"}),
        # 期次清单是 articles 的子资源：fnmatch 本就把 `articles*` 一起吃，所以新登记不
        # 负责放行它（谁能读条目的口径在服务层 releases_of(for_reader=)）
        ("GET", "/api/content/articles/a1/releases", {"article:manage"}),
        # 提升通道只新落在 article:manage：只勾审核/发布权的人推不出内容草稿，读者也推不出
        ("POST", "/api/content/promotions", {"article:manage"}),
        ("GET", "/api/content/promotions/match", {"article:manage"}),
    ]
    for method, path, expect in cases:
        assert allowed(method, path) == expect, f"{method} {path} -> {allowed(method, path)} != {expect}"


def test_promotion_channel_is_not_widened_to_review_or_publish():
    """提升通道只能落在 article:manage：把它接到 reviews*/publish* 那些码上等于新开后门。"""
    perms = _apply_release_patterns(_perm_rows(_read_migration()))
    holders = {code for code, r in perms.items()
               if any(_match_path(p, "/api/content/promotions") for p in r["patterns"])
               and "POST" in r["methods"]}
    assert holders == {"article:manage"}, holders


def test_review_and_publish_are_human_only():
    """审核与发布 ai_access='none': 对外内容代表公司立场, 必须有人负责。"""
    perms = _perm_rows(_read_migration())
    assert perms["article:review"]["ai_access"] == "none"
    assert perms["article:publish"]["ai_access"] == "none"
    assert perms["article:manage"]["ai_access"] == "write"


def test_every_perm_code_hangs_on_a_registered_menu():
    """权限点必须挂在已登记的菜单上, 否则角色页渲染不出（配了也白配）。"""
    sql = _read_migration()
    menu_block = sql.split("INSERT INTO adh_menu_registry", 1)[1].split("ON DUPLICATE KEY UPDATE", 1)[0]
    registered = set(re.findall(r"'(content:[a-z-]+)'", menu_block))
    for code, row in _perm_rows(sql).items():
        assert row["menu_key"] in registered, f"{code} 的 menu_key={row['menu_key']} 悬空"

# ── 6. 附件通道（kind=file）─────────────────────────────────────────────────────

class FakeStorage:
    """对象存储替身: 只记 key→字节, 失败可注入（不真连 OSS）。"""

    def __init__(self, *, fail_upload=False, fail_delete=False, missing=()):
        self.objects = {}
        self.fail_upload = fail_upload
        self.fail_delete = fail_delete
        self.missing = set(missing)      # download_bytes 取不到的 key

    def upload_bytes(self, key, data, content_type=""):
        if self.fail_upload:
            raise RuntimeError("SimulatedError: bucket unreachable")
        self.objects[key] = data

    def download_bytes(self, key):
        if key in self.missing:
            return None
        return self.objects.get(key)

    def delete(self, key):
        if self.fail_delete:
            raise RuntimeError("SimulatedError: delete denied")
        self.objects.pop(key, None)
        return True


@pytest.fixture
def store(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(svc, "get_object_storage", lambda: fake)
    return fake


def _seed_file(db, *, kind="file"):
    aid, _ = _seed(db)
    db.articles[aid]["kind"] = kind
    return aid


def test_gate_blocks_file_kind_without_attachment(db):
    """附件形态没文件 = 硬阻断提交（否则门户点进去是个打不开的空条目）。"""
    aid = _seed_file(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert exc.value.status == 422
    assert "附件形态" in exc.value.detail


def test_upload_then_submit_passes_file_gate(db, store):
    aid = _seed_file(db)
    item = svc.upload_file(aid, "白皮书.pdf", b"%PDF-1.7 fake", "author")
    assert item["label"] == "PDF" and item["size"] == len(b"%PDF-1.7 fake")
    assert "object_key" not in item, "对象存储 key 不得进响应体（护栏 §7）"
    out = svc.submit_review(aid, "author")
    assert out["status"] == "in_review"
    # 附件里的文字扫不到就得说"没扫"并让审核人逐条确认，不能让门禁看着像已经看过
    assert any(w["code"] == "attachment_unscanned" for w in out["warnings"]), out["warnings"]
    with pytest.raises(svc.ContentError) as exc:
        svc.approve_review(aid, "reviewer", comment="", acknowledged=[])
    assert "提醒没确认完" in exc.value.detail


def test_upload_rejects_unsupported_type_empty_and_oversize(db, store):
    aid = _seed_file(db)
    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(aid, "脚本.exe", b"MZ....", "author")
    assert "不支持的附件类型" in exc.value.detail
    assert not store.objects and not db.files, "校验失败不得留下行或对象"

    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(aid, "空表.xlsx", b"", "author")
    assert "0 字节" in exc.value.detail

    big = b"x" * (svc.MAX_CONTENT_FILE_SIZE + 1)
    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(aid, "超大.pptx", big, "author")
    assert "超过单文件上限" in exc.value.detail
    assert not db.files


def test_upload_failure_rolls_back_row_and_hides_raw_error(db, store, monkeypatch):
    """上传失败必须撤销登记, 且对外不回原始异常文本（护栏 §7: 细节只进日志）。"""
    aid = _seed_file(db)
    monkeypatch.setattr(store, "fail_upload", True)
    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(aid, "报告.docx", b"PK....", "author")
    assert exc.value.status == 502
    assert "SimulatedError" not in exc.value.detail and "bucket" not in exc.value.detail
    assert not db.files, "失败后不能留悬空附件行"


def test_duplicate_filename_and_over_count_rejected(db, store):
    aid = _seed_file(db)
    svc.upload_file(aid, "报告.pdf", b"a", "author")
    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(aid, "报告.pdf", b"b", "author")
    assert "同名附件" in exc.value.detail

    for i in range(svc.MAX_CONTENT_FILES_PER_ARTICLE - 1):
        svc.upload_file(aid, f"附表{i}.xlsx", b"x", "author")
    assert len(svc.list_files(aid)) == svc.MAX_CONTENT_FILES_PER_ARTICLE
    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(aid, "再多一个.pdf", b"x", "author")
    assert "最多挂" in exc.value.detail


def test_upload_requires_existing_article(db, store):
    """附件按条目归属: 条目不存在时直接拒（没有"先传后挂"的暂存区）。"""
    with pytest.raises(svc.ContentError) as exc:
        svc.upload_file(0, "a.pdf", b"x", "author")
    assert exc.value.status == 404
    assert not store.objects


def test_detail_and_review_draft_carry_file_list(db, store):
    aid = _seed_file(db)
    svc.upload_file(aid, "临床图谱.html", b"<p>x</p>", "author")
    for out in (svc.get_article(article_id=aid, can_edit=True, revision="head"),
                svc.review_draft(aid)):
        files = out["files"]
        assert [f["filename"] for f in files] == ["临床图谱.html"]
        assert files[0]["label"] == "HTML"
        assert all("object_key" not in f and "mime" in f for f in files)


def test_download_target_scoped_to_article(db, store):
    """下载寻址必须带条目限: 别的条目的 file_id 拿来就是 404（不能凭 id 跨条目取文件）。"""
    aid = _seed_file(db)
    other = _seed_file(db)
    f = svc.upload_file(aid, "a.pdf", b"x", "author")
    with pytest.raises(svc.ContentError) as exc:
        svc.file_for_download(other, f["file_id"], allow_draft=True)
    assert exc.value.status == 404
    # allow_draft=True 是本用例的“编排者视角”：发布门另有用例锁定，这里只验跨条目隔离
    assert svc.file_for_download(aid, f["file_id"], allow_draft=True)["filename"] == "a.pdf"


def test_attachment_download_is_gated_by_publish_state(db, store):
    """未发布条目的附件不得被读者取走：必须卡在服务层。

    为什么权限码不够用：``article:read`` 的 GET pattern 写的是
    ``/api/content/articles*``，而中间件用 fnmatch（``*`` 连 ``/`` 一起吃），
    分不出“列表”与“某条目的附件子资源”。首页资讯区对全员开放后，
    没有这道门就等于任何人拿 slug + file_id 就能下走 Word/PDF 原稿。
    """
    aid = _seed_file(db)
    f = svc.upload_file(aid, "内部原稿.docx", b"x", "author")
    with pytest.raises(svc.ContentError) as exc:
        svc.file_for_download(aid, f["file_id"])
    assert exc.value.status == 404, "草稿附件对读者一律 404，不得区分“没发布”与“不存在”"

    # 编排者/审核人视角照旧可下（看不到稿子就没法编排与审核）
    assert svc.file_for_download(aid, f["file_id"], allow_draft=True)["filename"] == "内部原稿.docx"

    # 发布后读者才能取
    db.articles[aid].update(status="published",
                            live_revision_id=db.articles[aid]["head_revision_id"])
    assert svc.file_for_download(aid, f["file_id"])["filename"] == "内部原稿.docx"

    # 下架后得回 404：门户入口消失了，附件不能继续可取
    db.articles[aid]["status"] = "archived"
    with pytest.raises(svc.ContentError) as exc:
        svc.file_for_download(aid, f["file_id"])
    assert exc.value.status == 404


def test_delete_file_reports_leftover_object(db, store):
    """对象删不掉时不能假装清干净: 行照删（下载入口先消失）, 但必须回 warning。"""
    aid = _seed_file(db)
    f = svc.upload_file(aid, "旧版.pptx", b"x", "author")
    store.fail_delete = True
    out = svc.delete_file(aid, f["file_id"], "author")
    assert out["deleted"] is True and out.get("warning")
    assert svc.list_files(aid) == []
    # 门禁此刻又变成硬阻断（附件形态没文件了）—— 这条判据必须跟着动
    with pytest.raises(svc.ContentError) as exc:
        svc.submit_review(aid, "author")
    assert "附件形态" in exc.value.detail


def test_delete_article_removes_objects_and_reports_leftover(db, store):
    """删条目不得把附件对象留在桶里；清不掉要说出来（只删行 = 旧直链仍可取到已删内容）。"""
    aid = _seed_file(db)
    svc.upload_file(aid, "临床图谱.pdf", b"%PDF", "author")
    assert store.objects, "上传后对象必须在"
    out = svc.delete_article(aid, "author")
    assert out["deleted"] is True and "warning" not in out, out
    assert store.objects == {}, "对象必须跟条目一起清掉"

    # 删除失败：条目依旧删成功（不拿存储故障阻塞主逻辑）, 但残留必须可见
    aid2 = _seed_file(db)
    svc.upload_file(aid2, "附录.xlsx", b"PK", "author")
    store.fail_delete = True
    out2 = svc.delete_article(aid2, "author")
    assert out2["deleted"] is True and "没删掉" in out2.get("warning", ""), out2
    assert svc.list_files(aid2) == [], "行照删, 下载入口得先消失"


def test_missing_object_is_not_disguised_as_no_attachment(db, store):
    """行在、对象没了: 下载侧要能区分"文件不可用"与"附件不存在"。"""
    aid = _seed_file(db)
    f = svc.upload_file(aid, "白皮书.pdf", b"x", "author")
    target = svc.file_for_download(aid, f["file_id"], allow_draft=True)
    assert store.download_bytes(target["object_key"]) == b"x"
    store.missing.add(target["object_key"])
    assert store.download_bytes(target["object_key"]) is None, "API 层据此回 410 而不是 404"


# ── 编排面草稿可见性与到期复核筛选（2026-11-13「审核发布」并入「内容管理」同批口径）──

def test_manage_scope_excludes_drafts(db):
    """未提交的工作稿不进取数编排面（管理员也一样）：草稿只在 mine 作用域回。

    拍板口径：草稿是个人工作稿，「内容管理」是已提交内容的编排面；
    服务端就一道门，不是靠前端不显示。
    """
    _seed(db, author="alice")
    assert svc.list_articles(status="all", can_edit=True)["total"] == 0
    mine = svc.list_articles(status="all", mine="alice", can_edit=True)
    assert [a["slug"] for a in mine["items"]] == ["scan-accuracy"]


def test_rejected_draft_goes_back_to_personal_scope(db):
    """提交后进入编排面；驳回退回 draft 就从编排面消失（回个人区，审核流水仍留痕）。"""
    aid, _ = _seed(db, author="alice")
    svc.submit_review(aid, "alice")
    assert svc.list_articles(status="all", can_edit=True)["total"] == 1

    svc.reject_review(aid, "boss", comment="样本区间与标题不一致")
    assert svc.list_articles(status="all", can_edit=True)["total"] == 0
    assert svc.list_articles(status="all", mine="alice", can_edit=True)["total"] == 1
    # 驳回原因必须作者可见（否则退回个人区就等于从所有人眼里消失）
    assert db.reviews[-1]["action"] == "reject" and db.reviews[-1]["comment"]


def test_manage_scope_rejects_explicit_draft_filter(db):
    """非 mine 显式筛 draft = 不合法组合：报错说清去哪看，不静默给空清单。"""
    _seed(db, author="alice")
    with pytest.raises(svc.ContentError) as exc:
        svc.list_articles(status="draft", can_edit=True)
    assert "我的草稿" in exc.value.detail


def test_expired_filter_matches_review_due_date(db):
    """到期复核走服务端同一谓词：只回 review_by 已过今天的条目，且不影响不带该筛选的清单。"""
    due, _ = _seed(db, status="published", live=500, author="alice")
    db.articles[due]["review_by"] = date.today() - timedelta(days=1)
    fresh, _ = _seed(db, status="published", live=500, author="alice")
    db.articles[fresh]["review_by"] = date.today() + timedelta(days=30)

    out = svc.list_articles(status="all", can_edit=True, expired=True)
    assert [a["id"] for a in out["items"]] == [due], out
    # 到期与否不是状态：不带 expired 时两条都在
    assert svc.list_articles(status="all", can_edit=True)["total"] == 2


