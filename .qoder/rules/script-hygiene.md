---
trigger: always_on
description: backend/scripts 是运维入口目录而非脚本垃圾场：只放运行必需的端口/启动文件与可重复执行的运维入口；一次性迁移/回填/修复脚本执行完必须同批删除（迁移真源在 docker/mysql/*_migration.sql），不得长期与项目代码混放；运行时代码零 import backend.scripts；删除前必须 grep 引用并同批修注释/测试。
---

# 运维脚本卫生（目录边界 + 一次性脚本生命周期）— 强制规范

> `backend/scripts` 里三类东西混放，是反复出现的维护成本来源：被启动链 `source` 的端口文件、
> 每次建模/排障都要重跑的可重复运维入口、以及**早该消失的一次性迁移脚本**。
> 第三类一旦常驻，就会变成"看不懂、不敢删、还被人当正门"的僵尸代码，
> 其注释与测试还会持续指向不存在的实现。以下为收敛规则，新增/删除脚本前必读。

## 1. 目录职责：backend/scripts 只允许三类文件

| 类别 | 判定 | 示例 |
|---|---|---|
| **① 运行必需** | 被 `start-all.sh` / `stop-all.sh` / `kill-ports.sh` / `setup-local.sh` 或 Docker 启动链直接消费 | `services.conf`（端口唯一权威登记）、`frontend_port.sh`、`start-service.sh` |
| **② 可重复运维入口** | 人工按需执行、**每次跑都可能有新产出**（建本体、登记资源、播种评测、巡检、探针） | `refresh_system_ontology.py`、`build_*_ontology.py`、`register_es_datasource.py`、`seed_eval_*.py`、`deploy_contract_bindings.py`、`check_alias_projection.py`、`probe_*.py`、`rotate_secret_key.py`、`sync_ontology_to_mirror.py` |
| **③ 一次性迁移/回填/修复** | 目标状态达成后再跑应报「0 处变更」，且其结论已落库 | `migrate_*`、`backfill_*`、`repair_*`、`retire_*` |

**第 ③ 类不得长期驻留本目录**：执行完成（并核验落库）后必须**同批删除**，
不在本目录"存档"。需要考古时用 `git log -- backend/scripts/<name>.py` 取回，历史仍在版本库。

## 2. 迁移真源是 SQL，不是 py 脚本

- **schema / 枚举 / 列语义等结构性变更**：真源 = `docker/mysql/*_migration.sql`（幂等、可重复执行），
  这是新环境唯一的建库路径；**禁止**只写一个 `migrate_xxx.py` 就算完成迁移。
- 数据搬运型回填（跨表推导、批量改写业务数据）无法用纯 SQL 表达时，才允许写一次性 py 脚本，
  但其**判定口径必须附在迁移 SQL 注释里**（值域/判据/边界），脚本本身执行完即删。
- py 迁移脚本**不写库名**（不得 `USE adh;`），库由连接配置决定；"已生效"只能按目标库现状核验，
  不以脚本退出码/打印为凭。

## 3. 脚本编写规范（保留在 ① ② 内的才需要长期维护）

- **dry-run 默认、`--apply` 才写库**；幂等（二次执行报 0 处变更）。
- 文件头 docstring 三段固定：**为什么需要它**（不是"脚本说明"而是缺陷/口径由来）、**用法**、
  **幂等/边界**（哪些行会被跳过、为什么宁缺勿错）。
- **凭据不进代码/不进 git**：需要口令/API Key 的登记一律走环境变量 + 脚本内 `get_datasource_conn` 解析
  （`register_es_datasource.py` 是正例：正因为凭据不能进迁移 SQL 才用脚本）。
- **不得被运行时代码 import**：`backend/app`、`backend/modules`、`backend/core` 一律不得
  `from backend.scripts import ...`（依赖方向单向：脚本 → 服务层）。脚本之间互相复用允许。

## 4. 删除与退役纪律（防悬空引用）

删除任何脚本前，必须逐条完成：

1. `grep -rn "<脚本名>"` 全仓核对引用面：`tests/`、`docker/mysql/*.sql` 注释、代码注释/docstring、
   `.qoder/rules|skills`、`docs/`、`SKILL.md`；
2. **同批**修改所有引用：注释改为"已一次性落实（脚本已下线）"的现状描述，不得留路径；
3. 若有测试**直接 import 该脚本**（如锁迁移幂等/口径同源的用例），该用例前提已不存在，
   **同批删除该用例**，不得保留对已删文件的 import（会直接把测试集打红）；
4. 删除后跑 `python -m py_compile`（剩余脚本全绿）+ 受影响用例。

保留判据（宁可保留）：被**运行期用户可见报错**直接指引的（如
`migrate_report_content_to_oss.py` 被 `report_service` 的报错文案指名）、被**测试 import 私有函数锁口径**的
（如 `backfill_dict_target_table` 的 `_split_agg_arg` 被字典一致性用例复用）、**跨环境仍需重放**的
（如 `migrate_workspace_user_paths.py` 的数据目录迁移）。

## 5. 命名约定（按用途区分，让人一眼看出寿命）

- 一次性（用完即删）：`migrate_*` / `backfill_*` / `repair_*` / `retire_*` —— 看到这些前缀就应视为"待清理"；
- 常驻运维：`build_*`（建本体/资产）、`seed_*`（播种评测/内容）、`probe_*`（实机探针取证）、
  `check_*` / `audit_*`（只报告不自动改）、`refresh_*`、`deploy_*`、`register_*`、`provision_*`、`rotate_*`。
- 临时草稿不要落在 `backend/scripts/`：放根目录忽略目录或 `logs/`（本地目录），**不得**用
  `.gitignore` 裸写 `scripts` 去"隐藏"它们——那会连 `backend/scripts` 一起吞掉（已发生过一次）。

## 6. 自查清单（提交前逐条回答）

- 我新增的脚本属于 ① ② 还是 ③？③ 类是否已执行完并**删除**？
- 结构性变更是否同时有 `docker/mysql/*_migration.sql`（新环境能起来）？
- 删脚本后全仓 grep 是否零悬空引用？测试集是否仍可收集（无 import 已删模块）？
- 脚本是否 `--apply` 才写库、二次执行 0 变更？docstring 是否写了"为什么"而不只是"做什么"？
- 有没有把凭据/连接串写进脚本或迁移 SQL？

## 7. 反模式速查

- ❌ 一次性迁移脚本常驻目录并配一个"锁幂等"的测试（脚本早已跑完，测试只是在养僵尸）；
- ❌ 只写 py 迁移脚本、不留 SQL（新环境建不起来，且无法审计口径）；
- ❌ 删脚本不修注释/测试引用（报错文案指向不存在的文件，测试 import 直接炸）；
- ❌ 运行时代码 `from backend.scripts import ...`（把运维工具变成运行依赖，打包/部署连带失败）；
- ❌ 用裸 `scripts` 规则"隐藏"临时脚本（会静默吞掉 `backend/scripts/**`，别人拉下来启动即报找不到文件）；
- ❌ 一次性脚本里写死库名 `USE adh;`（环境库是 `adh2` 时必须静默打错 schema）。