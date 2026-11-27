"""刷新 AS-BOT 系统能力本体（kind='system'）到当前代码事实。

为什么需要它：系统能力本体是整份 doc 由声明目录（
`backend/modules/catalog/services/system_ontology_catalog.py`）全量重算的派生
对象——代码里的动作/规则/场景一变，active doc 就必须跟着换代，否则契约漂移。

用法：
    ./venv/bin/python -m backend.scripts.refresh_system_ontology            # dry-run：diff + 契约漂移
    ./venv/bin/python -m backend.scripts.refresh_system_ontology --apply    # 快照+落库+级联
    ./venv/bin/python -m backend.scripts.refresh_system_ontology --apply --skip-kb

边界（P4 版本化发布）：
    本脚本只是薄编排——dry-run 输出对象 diff 与契约漂移；--apply 只调
    `ontology_release_service.release_system_ontology`（快照旧版为 archived 版本行、
    落 `adh_ontology_release_runs` 发布日志、整份重算落库 + 级联 + 核验）。
    不在脚本里写建模内容，也不直接拼 UPDATE/INSERT 业务表。

失败口径（no-silent-degradation）：
    列不存在、M2/M3/M4 校验有 error、知识库同步失败、对象表行数与 doc 不一致，
    一律打印原因并以非零退出码结束，不"看起来成功"。发布失败时 release 行留
    failed + 已发生副作用清单（不得假回滚成"什么都没发生"）。
"""

from __future__ import annotations

import argparse
import sys

from backend.modules.catalog.services import ontology_release_service as release
from backend.modules.catalog.services import system_ontology_catalog as catalog


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新 AS-BOT 系统能力本体")
    parser.add_argument("--apply", action="store_true", help="落库并级联（默认只 dry-run）")
    parser.add_argument("--skip-kb", action="store_true", help="跳过知识库同步")
    parser.add_argument("--actor", default="refresh_script", help="发布发起人（审计用）")
    args = parser.parse_args(argv)

    try:
        # dry-run 与 apply 共用同一报告构造（diff/漂移/校验口径不漂移）
        report = release.release_system_ontology(
            actor=args.actor, dry_run=not args.apply, skip_kb=args.skip_kb)
    except release.ReleaseError as exc:
        _fail(str(exc))
        return 1  # 不可达；_fail 已退出
    except ValueError as exc:
        _fail(str(exc))
        return 1

    diff = report["diff"]
    print(f"目标模型: {report['model_id']} 「{report['model_name']}」")
    print(f"对象数: {diff['old_count']} → {diff['new_count']}")
    print(f"新增 {len(diff['added'])}: {', '.join(diff['added']) or '-'}")
    print(f"移除 {len(diff['removed'])}: {', '.join(diff['removed']) or '-'}")
    print(f"变更 {len(diff['changed'])}: {', '.join(diff['changed']) or '-'}")
    print(f"规则 {report['rules']} 条 / 场景 {report['scenarios']} 条 / "
          f"动作 {report['actions']} 个")
    if report["contract_drift_detected"]:
        print("[WARN] 契约漂移（代码注册表 vs 当前生效 doc）:")
        for err in report["contract_drift"]:
            print(f"  - {err}")
        if not args.apply:
            print("（dry-run 提示：--apply 后漂移会随本次发布消除）")

    if not args.apply:
        print("dry-run 结束（加 --apply 才会落库）")
        return 0

    print(f"发布完成: release_run_id={report.get('release_run_id')} "
          f"object_rows={report.get('object_rows')}")
    for line in report.get("side_effects") or []:
        print(f"  · {line}")
    for w in (report.get("result") or {}).get("validation_warnings") or []:
        print(f"  告警: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
