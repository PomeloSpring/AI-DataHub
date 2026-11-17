"""登记「供应商直连大模型」并建好对应执行层（dry-run 默认，幂等可重跑）。

背景:
  Qoder CLI 的 BYOK 由组织策略裁决（企业侧可整体禁用），因此「让 Agent 用百炼」有第二条
  通道：`claude-agent-sdk` 走 Anthropic 兼容协议直连供应商，凭据取自模型中心
  （`adh_llm_models`），不依赖 Qoder 账号。本脚本把这条链路需要的两条记录一次配齐。

口径:
  1. **api_key 只从环境变量读**（默认 `DIRECT_LLM_API_KEY`），绝不写进命令行历史、脚本
     正文或迁移 SQL；入库前用 `crypto.encrypt_password` 加密，与 platform 侧写入口径一致。
  2. **执行层建成 inactive**：`get_default_external_layer()` 取「第一个 healthy+active」层，
     若这里建成 active 就等于替全站会话改道。启用与绑定工作空间必须由管理员在页面上做。
  3. **不动 `is_default`**：模型中心的默认行会被自研 agent 的 `get_llm_config()` 消费，
     改它 = 改现有 LLM 走向，属于另一个决策，脚本只提供 `--set-default` 显式开关。
  4. `--probe` 会向解析出的 Anthropic 端点发一次最小请求（含 tool_use），
     验证「地址归一 + 密钥 + 模型」三者配套；不通就报错，不落库。

用法（repo 根目录）:
    DIRECT_LLM_API_KEY=sk-xxx venv/bin/python -m backend.scripts.provision_direct_llm \
        --base-url https://<host>/compatible-mode/v1 --model qwen3.7-plus --probe
    ... --apply        # 校验通过后落库
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from backend.common.db import execute_query, execute_insert, execute_write

LAYER_NAME = "cli-claude-direct"
MODEL_NAME_PREFIX = "百炼直连"


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_layer_id() -> int:
    rows = execute_query("SELECT COALESCE(MAX(id),0) AS m FROM adh_execution_layers") or []
    return int((rows[0].get("m") if rows else 0) or 0) + 1


def probe(base_url: str, api_key: str, model: str, timeout: int = 60) -> dict:
    """实测 Anthropic 端点可用性与工具调用能力（fail-loud，返回诊断摘要）。"""
    from backend.modules.mind.execution.anthropic_direct import anthropic_base_url

    url = anthropic_base_url(base_url) + "/v1/messages"
    body = {
        "model": model, "max_tokens": 64,
        "tools": [{"name": "ping", "description": "回显探针",
                   "input_schema": {"type": "object", "properties": {"v": {"type": "string"}}}}],
        "messages": [{"role": "user", "content": "必须调用 ping 工具，参数 v=1。"}],
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"探针失败 HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001 — 网络/解析失败必须显式暴露原因
        raise SystemExit(f"探针失败 {type(exc).__name__}: {exc}") from exc
    blocks = [(c.get("type"), c.get("name")) for c in data.get("content") or []]
    if data.get("stop_reason") != "tool_use":
        raise SystemExit(f"端点可用但未产生 tool_use（stop_reason={data.get('stop_reason')}, "
                         f"blocks={blocks}）；Agent 链路依赖工具调用，该模型不适合做直连模型")
    return {"url": url, "stop_reason": data.get("stop_reason"), "blocks": blocks,
            "usage": data.get("usage")}


def plan(args) -> dict:
    """生成两条待写记录（含地址归一结果），供 dry-run 打印与 --apply 复用。"""
    from backend.modules.mind.execution.anthropic_direct import anthropic_base_url

    api_key = os.environ.get(args.api_key_env or "DIRECT_LLM_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(f"未取到 API Key：请设置环境变量 {args.api_key_env or 'DIRECT_LLM_API_KEY'}"
                         "（脚本不接受命令行传密钥，避免进 shell 历史）")
    name = args.name or f"{MODEL_NAME_PREFIX} · {args.model}"
    return {
        "model_row": {
            "name": name, "provider": args.provider, "base_url": args.base_url,
            "model_name": args.model, "context_window": args.context_window,
            "max_tokens": args.max_tokens, "supports_thinking": 1 if args.supports_thinking else 0,
            "is_default": 1 if args.set_default else 0,
        },
        "anthropic_url": anthropic_base_url(args.base_url),
        "layer_row": {
            "name": LAYER_NAME,
            "display_name": args.layer_display_name,
            "description": ("Anthropic 协议直连模型供应商（凭据取自模型中心），不依赖 Qoder 账号与 "
                            "Credits；默认停用，需在「执行层」页启用并绑定工作空间后才接管会话。"),
            "layer_type": "cli", "status": "inactive", "source": "manual",
            "config": {"mode": "sdk", "cli_name": "claude", "timeout": args.timeout},
        },
    }


def apply(args, p: dict) -> list[str]:
    """幂等落库：按 name 命中则更新，否则插入。返回逐条动作说明。"""
    from backend.common.crypto import encrypt_password

    actions: list[str] = []
    m = p["model_row"]
    api_key = os.environ.get(args.api_key_env or "DIRECT_LLM_API_KEY", "").strip()
    sealed = encrypt_password(api_key)
    existing = execute_query("SELECT id FROM adh_llm_models WHERE name = %s",
                             (m["name"],), fetchone=True)
    if existing:
        execute_write(
            "UPDATE adh_llm_models SET provider=%s, base_url=%s, api_key=%s, model_name=%s, "
            "model_id=%s, context_window=%s, max_tokens=%s, supports_thinking=%s, is_default=%s, "
            "is_active=1, updated_at=%s WHERE id=%s",
            (m["provider"], m["base_url"], sealed, m["model_name"], m["model_name"],
             m["context_window"], m["max_tokens"], m["supports_thinking"], m["is_default"],
             _now(), existing["id"]))
        actions.append(f"adh_llm_models 更新 id={existing['id']}（{m['name']}）")
    else:
        row_id = execute_insert(
            "INSERT INTO adh_llm_models (name, provider, model_id, base_url, api_key, model_name, "
            "context_window, max_tokens, supports_thinking, is_default, is_active, "
            "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
            (m["name"], m["provider"], m["model_name"], m["base_url"], sealed, m["model_name"],
             m["context_window"], m["max_tokens"], m["supports_thinking"], m["is_default"],
             _now(), _now()))
        actions.append(f"adh_llm_models 新增 id={row_id}（{m['name']}，api_key 已加密）")

    layer = p["layer_row"]
    hit = execute_query("SELECT id, status FROM adh_execution_layers WHERE name = %s",
                        (layer["name"],), fetchone=True)
    if hit:
        execute_write(
            "UPDATE adh_execution_layers SET display_name=%s, description=%s, layer_type=%s, "
            "config=%s, source=%s, updated_at=%s WHERE id=%s",
            (layer["display_name"], layer["description"], layer["layer_type"],
             json.dumps(layer["config"], ensure_ascii=False), layer["source"],
             _now(), hit["id"]))
        actions.append(f"adh_execution_layers 更新 id={hit['id']}（状态保持 {hit['status']}，不改）")
    else:
        lid = _next_layer_id()
        execute_insert(
            "INSERT INTO adh_execution_layers (id, name, display_name, description, layer_type, "
            "config, status, source, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (lid, layer["name"], layer["display_name"], layer["description"], layer["layer_type"],
             json.dumps(layer["config"], ensure_ascii=False), layer["status"], layer["source"],
             _now(), _now()))
        actions.append(f"adh_execution_layers 新增 id={lid}（{layer['name']}，status=inactive）")

    # 配置缓存 5min TTL，写完立刻失效，避免管理页/解析读到旧清单
    try:
        from backend.common.ttl_cache import model_config_cache
        model_config_cache.invalidate("llm_list")
    except Exception:  # noqa: BLE001 — 缓存失效失败不影响已落库事实
        pass
    return actions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="登记供应商直连模型并建对应执行层")
    ap.add_argument("--base-url", required=True, help="模型中心里要登记的 API 地址（OpenAI 兼容或 Anthropic 兼容均可）")
    ap.add_argument("--model", required=True, help="供应商侧模型名，如 qwen3.7-plus")
    ap.add_argument("--provider", default="openai", help="模型中心 provider 标签（默认 openai）")
    ap.add_argument("--name", default="", help="模型中心显示名（默认自动生成）")
    ap.add_argument("--layer-display-name", default="Claude 直连（百炼）")
    ap.add_argument("--context-window", type=int, default=200000)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--supports-thinking", action="store_true", default=True)
    ap.add_argument("--set-default", action="store_true",
                    help="把该模型设为模型中心默认（会改变自研 agent 的 LLM 走向，需显式指定）")
    ap.add_argument("--api-key-env", default="DIRECT_LLM_API_KEY")
    ap.add_argument("--probe", action="store_true", help="落库前实测端点 + tool_use 可用性")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省只打印计划）")
    args = ap.parse_args(argv)

    p = plan(args)
    if args.probe:
        info = probe(p["anthropic_url"], os.environ.get(args.api_key_env, ""), p["model_row"]["model_name"])
        print("探针通过:", json.dumps(info, ensure_ascii=False))

    print(json.dumps(p, ensure_ascii=False, indent=2))
    if not args.apply:
        print("\n[dry-run] 未写库。确认无误后加 --apply 执行。")
        return 0
    for line in apply(args, p):
        print("✓", line)
    print("\n后续（必须人工完成，脚本不替你做）:\n"
          "  1. 「执行层」页把 cli-claude-direct 启用并绑定目标工作空间；\n"
          "  2. 「AS-BOT」或聊天框选择该直连模型（模型引用形如 openai/qwen3.7-plus）；\n"
          "  3. 重启 web 让执行层清单刷新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
