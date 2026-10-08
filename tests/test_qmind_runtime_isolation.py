"""qmind 运行时独立性 + 同步失败显式暴露 — 回归锁定.

背景: qmind CLI 曾命中宿主机 ~/.cache 旧二进制短路自动下载、HOME 下旧账号
credentials 与项目凭据串源; kb 同步失败被吞成 synced=0 + HTTP 200(错因只在
服务端日志), 用户看不到原因。本文件锁定(qodercn 口径):
  1. CLI 入口只认项目 runtime/qmind-cli 的 npm 缓存(不读宿主机用户缓存);
  2. CLI 子进程 HOME/XDG/QMIND_HOME 收敛到 runtime/qmind/home;
  3. 所有 CLI 调用成对注入 --sash/--dashboard 指向 qoder.cn 网关;
  4. 同步失败错因脱敏后随响应回传, 全部失败 API 层转 502(no-silent-degradation).
"""

import pytest

from backend.modules.mind.rag import qmind_retriever as qr
from backend.modules.catalog.services import ontology_kb_sync as ks


# ── 运行时隔离 ─────────────────────────────────────────────────────

class TestQmindRuntimeIsolation:
    def test_cli_entry_stays_in_project_runtime(self):
        """CLI 入口只认项目 runtime/qmind-cli 的 npm 缓存, 不得回读宿主机用户目录(~/.cache 等)."""
        entry = qr.qmind_cli_entry()
        s = str(entry)
        assert "runtime" in s and "qmind-cli" in s, f"CLI 入口须落在项目 runtime/qmind-cli: {s}"
        home_prefix = str(qr.Path.home())
        assert not s.startswith(home_prefix), f"禁止读宿主机用户目录: {s}"

    def test_cli_argv_targets_qodercn_gateway(self):
        """所有 CLI 调用成对注入 --sash/--dashboard 指向 qoder.cn(qodercn)网关."""
        argv = qr.qmind_cli_argv(["notebook", "list"])
        assert argv[0] == str(qr._NODE_BIN), "由项目内 node 驱动"
        assert argv[1] == str(qr.qmind_cli_entry())
        assert argv[-4:] == ["--sash", "https://openapi.qoder.com.cn",
                             "--dashboard", "https://qoder.cn"]

    def test_cli_argv_target_overridable(self, monkeypatch):
        monkeypatch.setenv("QMIND_SASH_TARGET", "https://sash.test")
        monkeypatch.setenv("QMIND_DASHBOARD_TARGET", "https://dash.test")
        argv = qr.qmind_cli_argv(["x"])
        assert argv[-4:] == ["--sash", "https://sash.test",
                             "--dashboard", "https://dash.test"]

    def test_cli_env_isolates_home_and_xdg(self, monkeypatch, tmp_path):
        """HOME/XDG/QMIND_HOME 一律收敛到 runtime/qmind/home, 不读宿主机 ~/.config、~/.cache."""
        rt = tmp_path / "runtime" / "qmind"
        monkeypatch.setattr(qr, "_RUNTIME_QMIND_DIR", rt)
        monkeypatch.setenv("QMIND_TOKEN", "")
        monkeypatch.setenv("QODERCN_PERSONAL_ACCESS_TOKEN", "pt-TESTONLYabcdef")
        monkeypatch.delenv("QODER_PERSONAL_ACCESS_TOKEN", raising=False)
        env = qr._cli_env()
        home = rt / "home"
        assert env["HOME"] == str(home)
        assert env["XDG_CONFIG_HOME"] == str(home / ".config")
        assert env["XDG_CACHE_HOME"] == str(home / ".cache")
        assert env["QMIND_HOME"] == str(home)
        assert home.is_dir(), "home 目录需提前建好"
        # 无显式 QMIND_TOKEN 时由项目 PAT 兜底注入(qodercn 变量优先)
        assert env["QMIND_TOKEN"] == "pt-TESTONLYabcdef"

    def test_cli_env_falls_back_to_legacy_pat_var(self, monkeypatch, tmp_path):
        """旧变量名 QODER_PERSONAL_ACCESS_TOKEN 仍兼容为兜底来源."""
        monkeypatch.setattr(qr, "_RUNTIME_QMIND_DIR", tmp_path / "runtime" / "qmind")
        monkeypatch.setenv("QMIND_TOKEN", "")
        monkeypatch.delenv("QODERCN_PERSONAL_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pt-LEGACYtoken000")
        env = qr._cli_env()
        assert env["QMIND_TOKEN"] == "pt-LEGACYtoken000"

    def test_cli_env_keeps_explicit_qmind_token(self, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "_RUNTIME_QMIND_DIR", tmp_path / "runtime" / "qmind")
        monkeypatch.setenv("QMIND_TOKEN", "jt-EXPLICITjobtoken")
        monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pt-OTHERtoken000")
        env = qr._cli_env()
        assert env["QMIND_TOKEN"] == "jt-EXPLICITjobtoken", "显式 QMIND_TOKEN 优先"

    def test_cli_env_sets_debug_token_for_custom_target(self, monkeypatch, tmp_path):
        """custom --sash/--dashboard 目标下 CLI 只读 QMIND_DEBUG_TOKEN, 须同置."""
        monkeypatch.setattr(qr, "_RUNTIME_QMIND_DIR", tmp_path / "runtime" / "qmind")
        monkeypatch.setenv("QMIND_TOKEN", "pt-TESTONLYabcdef")
        monkeypatch.delenv("QMIND_DEBUG_TOKEN", raising=False)
        env = qr._cli_env()
        assert env["QMIND_DEBUG_TOKEN"] == "pt-TESTONLYabcdef"
        assert env["QMIND_NON_INTERACTIVE"] == "1", "禁止服务器环境弹交互登录"


# ── Node 版 CLI 错误提取(错误以 JSON 打在 stdout, stderr 常为空) ─────

class TestNodeCliErrorExtraction:
    def test_extract_err_parses_stdout_json_error(self):
        out = '{"error": {"code": "AUTH_REQUIRED", "message": "no credentials found"}}'
        err = qr._extract_err(out, "")
        assert err == "AUTH_REQUIRED: no credentials found"

    def test_extract_err_falls_back_to_stderr(self):
        assert qr._extract_err("", "boom on stderr") == "boom on stderr"

    def test_extract_err_falls_back_to_raw_stdout(self):
        assert qr._extract_err("plain text failure", "") == "plain text failure"

    def test_extract_err_never_returns_empty(self):
        assert qr._extract_err("", "") == "rc 非 0 且无错误输出"

    def test_is_unavailable_error_classifies_node_codes(self):
        """forbidden/not_found 触发 notebook 重建; 凭据类/网络类不得触发."""
        assert qr.is_unavailable_error("FORBIDDEN: insufficient notebook permission")
        assert qr.is_unavailable_error("NOT_FOUND: notebook not_found")
        assert qr.is_unavailable_error('reason: {"errorCode":"Forbidden"}')
        assert not qr.is_unavailable_error("AUTH_REQUIRED: no credentials found")
        assert not qr.is_unavailable_error("REMOTE_ERROR: gateway timeout")


# ── 失败显式暴露(可诊断但不泄凭据) ────────────────────────────────

class TestSyncFailureSurfaces:
    def test_safe_err_masks_token_forms(self):
        assert ks._safe_err("exchange pt-SECRETVAL123 failed") == "exchange *** failed"
        assert ks._safe_err("job jt-abc123 expired") == "job *** expired"
        assert "SECRETVAL" not in ks._safe_err("bad pt-SECRETVAL123456")

    def test_upload_markdown_surfaces_cli_error(self, monkeypatch):
        """upload 失败透传 CLI 错因, 不再只返回 False(返回空)."""
        monkeypatch.setattr(ks, "_run_cli_ex",
                            lambda args: (None, 'exchange returned 400: {"errorCode":"BadRequest"}'))
        ok, err = ks._upload_markdown("nb-1", "t.md", "# md")
        assert ok is False
        assert "BadRequest" in err

    def test_sync_failure_returns_redacted_error(self, monkeypatch):
        """全部目标失败: 返回体带 error(脱敏), 供 API 层转 502 / 前端展示."""
        monkeypatch.setattr("backend.modules.catalog.services.ontology_service.get_model",
                            lambda mid: {"id": mid, "kind": "system", "datasource_id": 0,
                                         "name": "sys", "status": "active"})
        monkeypatch.setattr(ks, "sync_targets_for_model",
                            lambda m: [{"id": 5, "name": "全局库", "notebook_id": "nb-1"}])
        monkeypatch.setattr(ks, "ensure_notebook", lambda kb: ("nb-1", ""))
        monkeypatch.setattr(ks, "_delete_old_sources", lambda nb, t: 0)
        monkeypatch.setattr(ks, "_render_cloud_doc", lambda m: "# cloud doc")
        monkeypatch.setattr(ks, "_upload_markdown",
                            lambda nb, t, md: (False, "failed to exchange personal token pt-LEAKME9876: 400"))
        monkeypatch.setattr(ks, "_write_state", lambda *a, **k: None)
        r = ks.sync_model_to_qmind(1)
        assert r["synced"] == 0
        assert "error" in r, "失败必须随响应回传错因(no-silent-degradation)"
        assert "exchange" in r["error"]
        assert "LEAKME9876" not in r["error"], "token 不得出站(护栏§7)"

    def test_api_returns_502_on_total_failure(self, monkeypatch):
        from fastapi import HTTPException
        from backend.modules.catalog.api import ontology as api_ontology
        monkeypatch.setattr(ks, "sync_model_to_qmind",
                            lambda mid: {"synced": 0, "targets": [], "error": "exchange 400"})
        with pytest.raises(HTTPException) as e:
            api_ontology.sync_model_kb(1)
        assert e.value.status_code == 502
        assert "exchange 400" in str(e.value.detail)

    def test_api_partial_success_keeps_200_with_error(self, monkeypatch):
        from backend.modules.catalog.api import ontology as api_ontology
        monkeypatch.setattr(ks, "sync_model_to_qmind",
                            lambda mid: {"synced": 1, "targets": ["全局库"], "error": "kb=6 upload failed"})
        r = api_ontology.sync_model_kb(1)
        assert r["synced"] == 1 and r["error"], "部分成功 200 但 error 仍随结果返回"


# ── 凭据类失败的可操作提示 ─────────────────────────────────────────

class TestCredHint:
    def test_cred_hint_recognizes_auth_failures(self):
        assert ks._cred_hint("exchange returned 400: BadRequest") == ks._CRED_HINT
        assert ks._cred_hint("401 Unauthorized") == ks._CRED_HINT
        assert ks._cred_hint("User not authenticated") == ks._CRED_HINT
        assert ks._cred_hint("connection timeout") is None

    def test_sync_failure_carries_cred_hint(self, monkeypatch):
        monkeypatch.setattr("backend.modules.catalog.services.ontology_service.get_model",
                            lambda mid: {"id": mid, "kind": "system", "datasource_id": 0,
                                         "name": "sys", "status": "active"})
        monkeypatch.setattr(ks, "sync_targets_for_model",
                            lambda m: [{"id": 5, "name": "全局库", "notebook_id": "nb-1"}])
        monkeypatch.setattr(ks, "ensure_notebook", lambda kb: ("nb-1", ""))
        monkeypatch.setattr(ks, "_delete_old_sources", lambda nb, t: 0)
        monkeypatch.setattr(ks, "_render_cloud_doc", lambda m: "# cloud doc")
        monkeypatch.setattr(ks, "_upload_markdown",
                            lambda nb, t, md: (False, "failed to exchange personal token: 400"))
        monkeypatch.setattr(ks, "_write_state", lambda *a, **k: None)
        r = ks.sync_model_to_qmind(1)
        assert r.get("error_hint") == ks._CRED_HINT

    def test_api_detail_includes_cred_hint(self, monkeypatch):
        from fastapi import HTTPException
        from backend.modules.catalog.api import ontology as api_ontology
        monkeypatch.setattr(ks, "sync_model_to_qmind",
                            lambda mid: {"synced": 0, "targets": [], "error": "exchange 400",
                                         "error_hint": ks._CRED_HINT})
        with pytest.raises(HTTPException) as e:
            api_ontology.sync_model_kb(1)
        assert ks._CRED_HINT in str(e.value.detail)
