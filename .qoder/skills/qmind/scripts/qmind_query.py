#!/usr/bin/env python3
"""qmind 云端知识库查询封装：list / retrieve。

用法（repo 根目录）:
  venv/bin/python .qoder/skills/qmind/scripts/qmind_query.py list
  venv/bin/python .qoder/skills/qmind/scripts/qmind_query.py retrieve --nb <id> -q "<问题>" [--top-k 5]

凭据: 自动从 backend/.env 的 QODER_PERSONAL_ACCESS_TOKEN 注入 QMIND_TOKEN（不回显）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)


def _load_token() -> str:
    env_path = os.path.join(REPO_ROOT, "backend", ".env")
    try:
        for line in open(env_path, encoding="utf-8"):
            m = re.match(r"\s*QODER_PERSONAL_ACCESS_TOKEN\s*=\s*(\S+)", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return ""


def _ensure_env() -> None:
    token = os.environ.get("QMIND_TOKEN") or _load_token()
    if not token:
        print("ERR: 缺凭据 —— backend/.env 无 QODER_PERSONAL_ACCESS_TOKEN，且环境无 QMIND_TOKEN", file=sys.stderr)
        raise SystemExit(2)
    os.environ.setdefault("QMIND_TOKEN", token)   # 不打印、不落盘
    os.environ.setdefault("QODER_PERSONAL_ACCESS_TOKEN", token)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出全部 notebook")
    p_ret = sub.add_parser("retrieve", help="检索一个 notebook")
    p_ret.add_argument("--nb", required=True, help="notebook_id")
    p_ret.add_argument("-q", "--question", required=True, help="业务语言问题")
    p_ret.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    _ensure_env()
    from backend.modules.mind.rag import qmind_retriever as qr

    if args.cmd == "list":
        nbs = qr.list_notebooks() or []
        out = [{k: nb.get(k) for k in ("notebook_id", "title", "description") if nb.get(k) is not None}
               for nb in nbs]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # --nb 为 notebook_id(uuid) 直查; 若传注册表整数 id 则走绑定检索
    if re.fullmatch(r"\d+", args.nb):
        result = qr.qmind_retrieve(args.question, 0, [int(args.nb)], top_k=args.top_k)
        out = {
            "rag_source": result.get("rag_source"),
            "knowledge_base": result.get("knowledge_base"),
            "count": result.get("count"),
            "degraded": result.get("degraded", False),
            "chunks": result.get("chunks") or [],
        }
    else:
        try:
            chunks = qr.retrieve_notebook_strict(args.nb, args.question, args.top_k)
        except Exception as e:  # noqa: BLE001 — CLI 失败显式暴露, 不回空假装成功
            print(f"ERR: qmind retrieve 失败（fail-loud）: {e}", file=sys.stderr)
            return 3
        out = {"notebook_id": args.nb, "count": len(chunks), "chunks": chunks}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
