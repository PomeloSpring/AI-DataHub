"""重建数据血缘：归一节点口径 + 重算资产链与 DAG 定义链（可重复运维入口）。

为什么需要它：
    血缘长期「不更新、关系不对」的根因有三层——① 资产链只在测试里调过、
    没有任何生产入口；② 同一张表有三种节点写法（裸名 / `stardb.表`（库名）/
    `数据源.表`），导致同名表双节点、边接不上；③ DAG/物化只靠执行时采集，
    一次没跑成功就没有边。本脚本把三件事收敛成一次可重复的重建：
    先归一历史节点 id（合并双节点并迁移边），再从元数据与 DAG 定义静态重算。

用法：
    ./venv/bin/python -m backend.scripts.rebuild_lineage            # 干跑，只统计
    ./venv/bin/python -m backend.scripts.rebuild_lineage --apply    # 落库
    ./venv/bin/python -m backend.scripts.rebuild_lineage --apply --reset   # 清空资产层重算（初始化）
    ./venv/bin/python -m backend.scripts.rebuild_lineage --apply --active-dag-only

幂等/边界：
    - 幂等：同节点/同边不重复创建，二次执行应为 0 新增；归一无法确定的
      节点（缺 datasource_id 且前缀不是数据源业务名/库名）原样保留并回带原因，
      不猜源；
    - `--reset` 只清派生资产层（产品/本体/口径/数据集/图表节点与资产链条边），
      表/字段节点与 transform 边（SQL 采集、DAG 定义、手工登记）不动；仅初始化/
      口径变更时用，清完再跑一次应 0 新增；
    - 只做派生视图，不改任何资产本身；
    - 库由连接配置决定，脚本不写库名。
"""

import argparse
import json
import sys

from backend.modules.gov.services.lineage_service import rebuild_lineage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建数据血缘（默认干跑）")
    parser.add_argument("--apply", action="store_true", help="写入数据库（默认只统计）")
    parser.add_argument("--workspace", type=int, default=0, help="工作空间 id，缺省 0")
    parser.add_argument("--active-dag-only", action="store_true",
                        help="只从已启用的 DAG 推导（默认含停用编排的历史定义）")
    parser.add_argument("--reset", action="store_true",
                        help="先清空派生的资产层（节点+边）再重算（初始化/口径变更用）")
    args = parser.parse_args(argv)

    result = rebuild_lineage(
        workspace_id=args.workspace,
        include_inactive_dag=not args.active_dag_only,
        dry_run=not args.apply,
        reset_asset=args.reset,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if not args.apply:
        print("\n[dry-run] 未写库；加 --apply 才会落库。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())