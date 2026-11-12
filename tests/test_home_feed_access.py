"""首页资讯区（把首页用起来）的读者口径权限面。

覆盖四条判据，都是本批改动真正要防的回归：

1. **读者码只给读**：`article:read` 的 api_pattern 覆盖 articles/categories/tags，
   method 只有 GET —— 写、审核、发布一条都不许顺带授出去。
2. **只勾编辑权的角色零回归**：`article:manage` 必须仍自带 GET（上一批的口径是
   "能管就能看"，只勾编辑权的人要能加载他管理的清单）。
3. **fnmatch 拦不住子资源，所以发布门必须开在服务层**：`/api/content/articles*`
   的 `*` 连 `/` 一起吃，`…/files/{id}/download` 会被同一条例外一并放行。这条用
   例把该事实钉死，并锁住下载链路确实把 `allow_draft` 交了下去 —— 否则首页一上线，
   任何人凭 slug + file_id 就能下走未发布草稿的 Word/PDF 原稿。
4. **迁移纪律**：登记读者码的迁移不许顺手重写 `article:manage`（真踩过：
   ON DUPLICATE 把上一批回填的 ai_action_key 覆盖成空串，Chat 里 6 个内容工具凭空消失）。

判定链与 role_service 的 DB 事实解耦（这里不连库），库侧生效由
`docker/mysql/home_feed_migration.sql` 的核验清单与实机验证把门。
"""

import ast
import pathlib
import re

from backend.common import api_permission as ap

REPO = pathlib.Path(__file__).resolve().parents[1]
API_FILE = REPO / "backend/modules/platform/api/content.py"
MIGRATION = REPO / "docker/mysql/home_feed_migration.sql"
RELEASE_MIGRATION = REPO / "docker/mysql/content_release_migration.sql"

# 与 home_feed_migration.sql 逐字对应的登记值（迁移改了这边必须同步改，否则配置漂移）
READ_PATTERNS = ("/api/content/articles*", "/api/content/categories*", "/api/content/tags*")


def _rules(patterns, methods):
    return [{"pattern": p, "method": m, "allowed": True}
            for p in patterns for m in methods]


READ_RULES = _rules(READ_PATTERNS, ["GET"])
MANAGE_RULES = _rules(READ_PATTERNS, ["GET", "POST", "PUT", "PATCH", "DELETE"])


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"未找到函数 {name}")


def _call_kwargs(node) -> set:
    """函数体内的调用及其参数名：`{(被调函数名, 参数名)}`，位置参数记成 `pos:<名>`。

    为什么不用字符串在 `ast.dump` 里搜 `user=user`：dump 的形状是 `keyword(arg='user', ...)`，
    写法一改断言就假失败。接线把门要按 AST 结构判，才经得起重构。
    """
    out = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        fname = getattr(n.func, "id", "") or getattr(n.func, "attr", "")
        for kw in n.keywords:
            if kw.arg:
                out.add((fname, kw.arg))
        for a in n.args:                     # _file_target(ident, file_id, user) 是位置传参
            if isinstance(a, ast.Name):
                out.add((fname, f"pos:{a.id}"))
    return out


def _param_names(node) -> set:
    return {a.arg for a in list(node.args.args) + list(node.args.kwonlyargs)}


# ── 1. 读者码只给读 ───────────────────────────────────────────────────────────

def test_reader_code_allows_portal_reads(monkeypatch):
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: READ_RULES)
    for path in ("/api/content/articles", "/api/content/articles/scan-accuracy-metric",
                 "/api/content/categories", "/api/content/tags"):
        assert ap.check_api_permission("viewer", "GET", path), path
        assert ap.check_api_permission("viewer", "GET", path + "/"), path   # 尾斜杠同判


def test_reader_code_gets_no_write_no_review_no_publish(monkeypatch):
    """读者码只有 GET：写草稿、提交审核、审核、发布、下架都必须拒。

    审核与发布本来就挂在独立前缀（/reviews*、/publish*），这里连 pattern 都不覆盖，
    所以拒的原因是"没有那条规则"而不是"方法不匹配" —— 两种都要成立。
    """
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: READ_RULES)
    assert not ap.check_api_permission("viewer", "POST", "/api/content/articles")
    assert not ap.check_api_permission("viewer", "DELETE", "/api/content/articles/x")
    assert not ap.check_api_permission("viewer", "POST", "/api/content/articles/x/submit")
    assert not ap.check_api_permission("viewer", "GET", "/api/content/reviews/pending")
    assert not ap.check_api_permission("viewer", "POST", "/api/content/publish/x")


# ── 2. 编辑权自带读，零回归 ───────────────────────────────────────────────────

def test_editor_code_still_carries_get(monkeypatch):
    """只勾「编辑内容条目」的角色必须能加载管理清单（上一批口径，不许被本批挤掉）。"""
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: MANAGE_RULES)
    assert ap.check_api_permission("knowledge_ops", "GET", "/api/content/articles")
    assert ap.check_api_permission("knowledge_ops", "POST", "/api/content/articles")
    # 但编辑权不含发布/审核（独立前缀），这条边界必须一直成立
    assert not ap.check_api_permission("knowledge_ops", "POST", "/api/content/publish/x")
    assert not ap.check_api_permission("knowledge_ops", "GET", "/api/content/reviews/pending")


# ── 3. fnmatch 拦不住子资源 → 发布门必须在服务层 ──────────────────────────────

def test_reader_pattern_does_cover_attachment_subpath():
    """**故意断言 fnmatch 拦不住**：`articles*` 连附件下载子路径一起吃。

    这条不是描述缺陷，而是把约束钉成用例：既然中间件分不出"列表"与"某条目的子资源"，
    发布状态判定就必须待在 `content_service.file_for_download(allow_draft=)` 里。
    有人想说"那把 pattern 写细一点"时，这条用例会先告诉他中间件做不到。
    """
    for p in ("/api/content/articles/x/files/1/download",
              "/api/content/articles/x/files/1/download-url"):
        assert ap._match_path("/api/content/articles*", p), p
        # reviews 前缀不在读者码里：审核人下草稿附件走自己的码，两条路不互借
        assert not ap._match_path("/api/content/articles*", "/api/content/reviews/x/files/1/download")


def test_attachment_download_passes_actor_and_gates_draft():
    """源码级接线：四个下载端点必须把身份交给附件寻址，且寻址把 allow_draft 交下去。

    只测服务层不够 —— 服务层默认 fail-closed，但若 API 层丢了读者的 user、
    或反过来把审核侧也当读者拦，缺陷会以两种相反方向出现（越权 / 审核拿不到稿子）。
    """
    tree = ast.parse(API_FILE.read_text(encoding="utf-8"))

    gate = _func(tree, "_file_target")
    gk = _call_kwargs(gate)
    assert ("file_for_download", "allow_draft") in gk, "附件寻址必须把 allow_draft 交给服务层"
    assert ("_resolve_id", "pos:user") in gk or ("_resolve_id", "user") in gk, \
        "条目解析必须带身份（读者只能解析在线条目）"
    assert "reviewer" in _param_names(gate), "审核侧要能显式放行草稿附件（看不到稿子的审核是空承诺）"

    for h in ("article_file_download", "article_file_download_url",
              "review_file_download", "review_file_download_url"):
        node = _func(tree, h)
        assert "user" in _param_names(node), f"{h} 必须解析调用者身份"
        assert ("_file_target", "pos:user") in _call_kwargs(node), f"{h} 必须把身份交给附件寻址"
    for h in ("review_file_download", "review_file_download_url"):
        assert ("_file_target", "reviewer") in _call_kwargs(_func(tree, h)), f"{h} 需显式 reviewer"


def test_reader_resolves_only_live_articles_for_related():
    """相关推荐按读者口径解析条目：未发布条目的 slug 不该被解析出 id（存在性探测）。"""
    src = API_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    rel = _func(tree, "read_related")
    assert "user" in _param_names(rel), "相关推荐必须拿到调用者身份"
    rk = _call_kwargs(rel)
    assert ("_resolve_id", "user") in rk or ("_resolve_id", "pos:user") in rk, \
        "相关推荐必须按身份解析条目"
    # 解析器自身口径：无身份=旧行为（写通道零变化），有身份=只认在线
    assert re.search(r'revision="head" if user is None else "live"', src), \
        "无身份走旧行为、有身份按在线解析 —— 两个方向都不能丢"


# ── 4. 迁移纪律（幂等迁移只能动自己拥有的列）──────────────────────────────────

def test_migration_registers_reader_without_touching_editor():
    """迁移登记 article:read，且**不得**把 article:manage 一起写进 VALUES。

    真踩过：article:manage 被带进 ON DUPLICATE 且本行 ai_action_key='' → 上一批
    回填的 6 个内容动作（content_draft.create 等）被覆盖成空串，Chat 里的内容
    创作工具对任何角色都消失，而页面功能一切正常（缺陷只在 AI 通道暴露）。
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "('article:read'," in sql, "读者码必须登记"
    assert "'home:feed'" in sql, "读者码必须挂菜单，否则角色页渲染不出（配了也白配）"
    assert "INSERT IGNORE INTO adh_role_perms" in sql and "'article:read'" in sql, \
        "存量角色默认授权漏配 = 上线后非 admin 首页资讯区凭空消失"
    # 只看 VALUES 元组首列：ai_note 正文里提到 article:manage 是合法说明文字，不算
    values_block = sql[sql.index("INSERT INTO adh_perm_registry"):sql.index("ON DUPLICATE KEY UPDATE")]
    registered = re.findall(r"^\('([a-z_-]+:[a-z]+)',", values_block, re.M)
    assert registered == ["article:read"], \
        f"本批只登记 article:read；多带的码都会被 ON DUPLICATE 覆盖它拥有的列：{registered}"
    assert "ai_action_key = VALUES(ai_action_key)" not in sql[sql.index("ON DUPLICATE KEY UPDATE"):], \
        "更新列里也不许出现 ai_action_key（那是上一批迁移拥有的列）"


def test_reader_code_ai_level_is_none_with_reason():
    """article:read 的 ai_access 必须是 none 且给出原因。

    它是"人能不能在首页看到资讯区"的可见性码；AI 读内容走 article:manage 下已登记的
    content_article.list / content_article.get。把它配成 read 会造出"配了级别却无消费方"
    的空承诺（tests/test_function_ai_access.py 的同一条守卫口径）。
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    row = sql[sql.index("('article:read',"):sql.index("ON DUPLICATE KEY UPDATE")]
    assert re.search(r"'none', ''", row), "ai_access 必须 none、ai_action_key 必须留空"
    assert "content_article.list" in row, "ai_note 要写清 AI 侧的读内容通道在哪，免得管理员误解配"


def test_home_feed_menu_shape():
    """菜单登记五要素齐（menu-permission-sync §2.1）：label/section/module/sort/perm_code。"""
    sql = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"\('home:feed',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*(\d+),\s*'([^']+)'", sql)
    assert m, "home:feed 登记行形状不符（label/section/module/sort/perm_code 缺一不可）"
    label, section, module, sort_, perm = m.groups()
    assert label == "首页资讯区" and section == "首页" and module == "home"
    assert perm == "article:read", "菜单显隐真值源是 perm_code 反查，挂错码=配了不生效"
    assert sort_.isdigit()


# ── 5. 期次清单与提升通道（本批新端点）──────────────────────────────────────

#: content_release_migration.sql 往 article:manage 追加的那条 pattern（与迁移逐字对应）
PROMOTION_PATTERN = "/api/content/promotions*"


def test_reader_can_read_release_history(monkeypatch):
    """历史期次清单是门户阅读能力，跟 articles* GET 同一条 pattern（不另加登记）。"""
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: READ_RULES)
    assert ap.check_api_permission("viewer", "GET", "/api/content/articles/x/releases")
    assert ap.check_api_permission("viewer", "GET", "/api/content/articles/x?release=2")
    # 但读者拿不到任何写能力：发布/切换展示版本都在独立前缀
    assert not ap.check_api_permission("viewer", "POST", "/api/content/publish/x")


def test_reader_code_never_covers_promotion_channel(monkeypatch):
    """提升通道是“写内容草稿”，读者码必须够不到。"""
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: READ_RULES)
    assert not ap.check_api_permission("viewer", "POST", "/api/content/promotions")
    assert not ap.check_api_permission("viewer", "GET", "/api/content/promotions/match")


def test_promotion_endpoint_registered_under_editor_code():
    """提升端点不新增权限码：它必须被 article:manage 追加后的 pattern 恰好接住。

    两头都钉：漏登记 = 勾了编辑权照样 403（空承诺）；多命中 = 只勾读者码也能提交（越权）。
    """
    sql = RELEASE_MIGRATION.read_text(encoding="utf-8")
    assert "CONCAT(api_pattern" in sql and PROMOTION_PATTERN in sql, \
        "迁移必须用 CONCAT 追加而不是重写 api_pattern（后者会覆写别的批次拥有的列）"
    assert "AND api_pattern NOT LIKE '%/api/content/promotions%'" in sql, "追加必须幂等可重跑"

    assert ap._match_path(PROMOTION_PATTERN, "/api/content/promotions")
    assert ap._match_path(PROMOTION_PATTERN, "/api/content/promotions/match")
    for p in READ_PATTERNS:
        assert not ap._match_path(p, "/api/content/promotions"), \
            f"{p} 不应连带放行提升端点（否则读者码就能写内容草稿）"
