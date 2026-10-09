---
trigger: always_on
description: FDE 迭代方法论（Forward Deployed Engineer）。本项目按 Palantir FDE 方式演进：eval 是本体的 CI、一切变更走 AS-BOT 直执行回路（菜单与功能权限码把关）、失败数据驱动建模 backlog；新增/调整 Chat、检索、本体能力时务必遵循。
---

# FDE 迭代方法论（Forward Deployed Engineer）— 强制规范

> 我们不是"写完功能再找用户"，而是像 Palantir FDE 一样贴着业务问题持续演进本体。
> 准确率不是模型属性，是**评测驱动的工程质量**。三因子：意图约束（intent 拒 SQL）× 确定性编译（planner）× 种子解析质量（别名/KB），任何一项改动都要能回答"eval 数据上前进了还是退步了"。

## 1. Eval 是本体的 CI（先度量，后优化）
- 评测是**项目内可复用的工程能力**（`backend/eval/`），不是 tests/ 下的附属脚本——生产侧（Celery 业务评测、管理页）要能正向导入，评测核心**不得**放 tests/ 供反向依赖。用例存元数据库 `adh_eval_cases`（页面可增删改查），运行结果存 `adh_eval_runs`/`adh_eval_results`，支持版本对比与发布前检查。
- 三个评测集（suite）共用一套契约/评分/对比/落库，各层只提供执行适配器：
  - `compile` = **语义层编译**（`intent→binding→plan`），断言解析档位/SQL 形状/拒收/候选回抛，不依赖 LLM 与真实数据行；
  - `retrieval` = **本体层检索**（真跑 `rag_retriever.retrieve_with_strategy`，含 GraphRAG/ontology_traversal），断言命中集合与**检索来源**分桶；
  - `llm` = **LLM 功能**（工具调用轨迹 / 出站脱敏 / 涉密拒绝），三种模式：`trace` 轨迹回放、`handler` 真调工具 handler、`live` 真调 LLM。
  **两类分桶语义不同**：`compile` 的 `dict_exact/dict_alias/phys_col` 是 planner 的名字解析档位，`retrieval` 的 `qmind_hit/graphrag/bm25` 是检索来源。报告里必须分开呈现，**不得拿编译集通过率为检索层背书**。
- **任何**触碰 prompt（`prompt_composer.py`）、别名/字典、planner、检索策略、本体导入导出的提交，必须跑 `venv/bin/python -m backend.eval.runner` 并对比基线：**总正确率与分 tag 正确率不得低于改前**；`tests/test_eval_baseline.py` 作为 pytest 门禁。
- 新解析/检索行为必须**先加 eval 用例再实现**（或同批落地）；只加功能不加用例视为未完成。用例覆盖维度以既有 tag 为准：exact/alias/enum/time_window/time_grain/template/cross_source/unbound/fuzzy/reject/unresolved(no_leak)/combo。
- 归因优先于调优：解析档位（`provenance.resolution_sources`）、检索来源（knowledge_search 的 `retrieval_source` + observability `kind="retrieval"` span）是决策数据源。**没有分桶数据不做任何检索/解析策略取舍**（如"双发是否拆回按需路由"只能由 eval 与命中率数据判定）。

## 2. 变更执行不造第二通道（AS-BOT 唯一回路）
- 一切对本体/字典/配置的**写操作**（含自进化产出的候选变更：补别名、建对象、改绑定）走**唯一回路**：菜单与功能权限码（`adh_role_perms` + `adh_perm_registry.ai_access`，经 `perm_link.require_write_perm` fail-closed）+ AS-BOT 工具授权 → **直执行** → 审计落库（`decided_by`/`approved_by` 由服务端注入，不从请求体信任身份）。旧「AS-BOT Action 提议→审批→执行」回路（`adh_as_bot_approvals`/动作矩阵）已退役，不得回潮。
- 新增自动化/自进化能力时**复用**该口径映射权限码（`metadata.sync`→`sync:manage`、`alias.approve/reject`→`ontology:save`、`dashboard.publish`→`dashboard:manage`、`product.contract_change`→`dataset:manage`），不得另建"直接 UPDATE 元数据表"的旁路。
- 执行成功 ≠ 全系统一致：涉及派生系统（图谱/qmind 文档/绑定表）的动作必须走服务层级联（见 ontology-modeling §1/§6），并有水位线或对账兜底（`reconcile()`）。

## 3. 失败数据驱动建模 backlog（自进化闭环）
- 解析失败/模糊被拒的业务词自动落入 `adh_alias_suggestions`（planner `unresolved_terms` → `alias_suggestion.record_unresolved_terms`，best-effort 永不影响取数主链路）；**建模待办由失败数据生成，不靠拍脑袋堆别名**。
- 审核经别名直审端点写回（`POST /api/as-bot/alias-suggestions/{id}/approve|reject`，`ontology:save` 把关后直执行）：字典行 aliases 或 active 模型对象 aliases（后者走 `save_draft` 级联重建图谱+重推知识库）。闭环 = 落入 → 审核(eval 佐证) → 回写 → 重评。
- 反复"未绑定走旁路/找不到对象"的对象缺口，应转化为本体建模项而不是检索侧 hack；巡检 `scripts/check_alias_drift.py` 输出派生物漂移报告。

## 4. Fail-safe 与降级守则
- 观测/队列/同步类改动**不得影响取数主链路**：全部 try/except 降级 + debug/warning，观测未开启即全链路 no-op（护栏 §9）。
- 宁缺勿错：解析拿不准就回抛候选让 LLM 确认，不静默绑定；云端知识库不可用/熔断（`_breaker_allowed`）透明回退本地 hybrid 并显式标注（`degraded`/`qmind_circuit_open`），行为"不劣化且可诊断"。
- 外部依赖（qmind CLI 等）按"子进程即脆弱"对待：超时、失败计数熔断、回退路径三件套缺一不可。

## 5. 阶段门与前置安全升级
- 结构性增强（如 Link 参与编译、实例级图谱、多跳）属 Phase 2：**必须在 Phase 0/1 的度量底座与基线就绪后，按 eval 薄弱点决定投入顺序**；未证明收益不做架构级改动。
- 若增强触碰护城河前置缺口（例：带 `db.table` 限定名/JOIN 侧的执行 SQL 形态，需先升级 `enforcer._extract_tables` 与 RLS 注入覆盖，护栏 §10），**安全升级先行，功能随后**——引入新 SQL 形态而 RLS 注入未覆盖即安全缺陷。
- 每次迭代收尾输出：eval 报告（总/分 tag/分来源档位）+ 新增红/修掉的红 + 是否触发回退决策（如拆双发）。

## 6. 相关门禁速查
- eval：`venv/bin/python -m backend.eval.runner`（`--suite compile|retrieval|llm` 选集，`--json` 供 CI，`--no-persist` 只跑不落库）
- 建模回归：见 `ontology-modeling.md` §8
- 护城河三件套：`tests/test_data_moat_enforcement.py` `tests/test_permission_enforcer.py` `tests/test_permission_e2e.py`
- 代码重启方生效：**web 单进程**（`backend/app/main.py`，绑 8001-8007/8012 全部契约端口，datamind/datacatalog 等旧分服务口径已退役）+ celery-worker/beat（uvicorn 无 --reload）
