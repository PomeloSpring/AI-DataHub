"""品牌设置（adh_brand_settings）读出口径 —— 登录页品牌事实的服务端一侧。

背景：登录页把应用名与标语写死在页面里，品牌设置改名/换图标/改标语都不跟着变。
修复同时补了 tagline 列（品牌标语），并把品牌读接口登记为匿名可访问
（登录页此刻还没有身份）。本文件锁定三件容易写错的事：
1. 默认值集合必须与前端 BrandSettings 字段一致（少一个字段前端就渲染成 undefined）；
2. 标语的空串是「管理员显式清空」，不许回落默认文案（与 app_name 的判空口径相反）；
3. 迁移幂等：加列走 information_schema 判定，重跑不覆盖人工改动。
"""

from unittest.mock import MagicMock

import pytest

from backend.modules.platform.api import brand as brand_api


def _stub_conn(monkeypatch, row):
    """把 _load/_save 里的 MySQL 连接换成可编程返回行的替身。"""
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    import backend.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: conn)
    return cur


def test_default_brand_covers_frontend_fields():
    assert set(brand_api._DEFAULT_BRAND) == {"app_name", "logo_url", "show_icon", "show_text", "tagline"}


def test_load_keeps_empty_tagline_as_cleared(monkeypatch):
    """tagline='' 是显式清空（登录页不显示标语），不得回落默认文案；app_name 空则回落默认名。"""
    _stub_conn(monkeypatch, {
        "app_name": "", "logo_url": "", "show_icon": 1, "show_text": 1, "tagline": "",
    })
    got = brand_api._load_brand_settings()
    assert got["app_name"] == brand_api._DEFAULT_BRAND["app_name"]
    assert got["tagline"] == ""


def test_load_falls_back_only_when_column_missing(monkeypatch):
    _stub_conn(monkeypatch, {
        "app_name": "数智中枢", "logo_url": "", "show_icon": 1, "show_text": 1, "tagline": None,
    })
    assert brand_api._load_brand_settings()["tagline"] == brand_api._DEFAULT_BRAND["tagline"]


def test_save_persists_tagline(monkeypatch):
    cur = _stub_conn(monkeypatch, None)
    settings = {**brand_api._DEFAULT_BRAND, "app_name": "数智中枢", "tagline": "让提问有可信答案"}
    brand_api._save_brand_settings(settings)
    sql, params = cur.execute.call_args[0]
    assert "tagline" in sql
    assert params[-1] == "让提问有可信答案"


def test_update_trims_and_invalidates_cache(monkeypatch):
    _stub_conn(monkeypatch, dict(brand_api._DEFAULT_BRAND))
    saved: dict = {}
    monkeypatch.setattr(brand_api, "_save_brand_settings", lambda s: saved.update(s))
    from backend.common.ttl_cache import brand_cache
    invalidated: list = []
    monkeypatch.setattr(brand_cache, "invalidate", lambda k: invalidated.append(k))

    out = brand_api.update_brand_settings(brand_api.BrandSettingsUpdate(tagline="  让提问有可信答案  "))
    assert out["tagline"] == "让提问有可信答案"
    assert saved["tagline"] == "让提问有可信答案"
    assert invalidated == ["settings"]


def test_migration_is_idempotent_and_default_matches_frontend():
    """加列必须走 information_schema 判定（MySQL 不支持 ADD COLUMN IF NOT EXISTS），
    且列默认值要与前端 lib/brand.ts 的 DEFAULT_TAGLINE 同值，否则首屏与保存后两个文案。"""
    import pathlib
    import re

    sql = (pathlib.Path(__file__).resolve().parents[1] / "docker" / "mysql" / "brand_tagline_migration.sql").read_text("utf-8")
    assert "information_schema.COLUMNS" in sql
    assert re.search(r"ADD COLUMN tagline VARCHAR\(\d+\) NOT NULL DEFAULT ''([^']+)''", sql), sql
    # 存量行由列 DEFAULT 落值：迁移里不许出现无条件 UPDATE 把已清空的标语顶回去
    assert "UPDATE adh_brand_settings" not in sql


def test_brand_read_route_is_anonymous():
    """登录页拉品牌时还没有身份：读口必须在公开清单里，否则 401 → 刷 token → 回 /login 重载成环。"""
    from backend.common import api_permission as ap

    assert ("GET", "/api/admin/brand") in ap._PUBLIC_ROUTES
    # 写口（改品牌）不得被顺手放行
    assert ("PUT", "/api/admin/brand") not in ap._PUBLIC_ROUTES


@pytest.mark.parametrize("path_", ["/api/admin/brand", "/api/admin/brand/"])
def test_middleware_serves_brand_read_without_identity(path_):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.common import api_permission as ap

    app = FastAPI()

    @app.get("/api/admin/brand")
    @app.get("/api/admin/brand/")
    def _brand():
        return dict(brand_api._DEFAULT_BRAND)

    @app.put("/api/admin/brand")
    def _brand_put():
        return {"ok": True}

    ap.add_api_permission_middleware(app)
    client = TestClient(app)

    assert client.get(path_).status_code == 200          # 匿名可读
    assert client.put(path_).status_code == 401          # 写仍要身份
