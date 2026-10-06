---
trigger: always_on
description: 本体建模规范（Ontology Modeling Spec）。本体是唯一语义事实源；新增/修改本体对象、指标、维度、术语、别名、枚举或上云文档时的强制标准。与 security-guardrails.md 互补：本规则管"建模正确"，护栏管"取数安全"。
---

# 本体建模规范（Ontology Modeling Spec）— 强制规范

> 本体是 ChatBI 的唯一权威语义层（对标 Palantir Ontology）。以下任一条被违反即视为建模缺陷，必须修复而非妥协。
> 事实源代码：`services/datacatalog/services/ontology_service.py`（canonical JSON）、`services/shared/semantics/`（planner/binding/models/intent）。

## 1. 事实源与派生分层（单一可编辑源）
- **canonical JSON doc 是唯一可编辑事实源**（`adh_ontology_models.json_content`）；YAML、内部 md（`to_md`）、RDF 图谱、qmind 上云文档（`to_cloud_md`）、`adh_ontology_objects` 展开行**全部是派生视图**，严禁独立编辑派生物。
- 修改本体只有一条路径：编辑 doc → `save_draft`/`activate` → 服务层自动级联（重算 execution_binding → 重展开 objects → 重写 bindings → 重建图谱 → 同步知识库）。**禁止**绕过级联直接 UPDATE 派生表。
- 枚举的唯一可编辑源是**维度字典 `value_labels`**；本体 `properties[].enum` 经 `sync_enums_to_dimensions` 单向联动到字典，图谱 `owl:oneOf` 只是只读投影。不得在图/文档侧独立改枚举。

## 2. 对象/属性建模要求
- 对象面向业务命名（订单/客户/案例），**不得直接照搬表名堆砌**；一个对象绑定一张 `primary_table`（宽对象或 SQL 模板承载复杂形状）。
- 属性（properties）**必须有业务中文名**：云端渲染与用户提示只暴露业务名，无业务名的属性不渲染（物理列名即泄露源）。
- `links[].join` 物理 JOIN 表达式仅存在于内部 md/图谱；**上云文档一律剔除**（`to_cloud_md` 白名单）。
- 对象命名/别名：`{key, display_name, aliases[]}`；别名维护原则见 FDE 规则（由失败数据驱动，不靠拍脑袋堆）。
- 数据源关联**以 `datasource_name` 为全局唯一标识**参与绑定解析（删除重建 id 会变，name 不变）；`datasource_id` 仅服务端使用，严禁进 LLM 视野或请求体。

## 3. 指标/维度字典绑定纪律
- `adh_metrics` / `adh_dimensions` 生效行**必须**有 `bound_object_key` 指向本体对象 key；指标须含 `formula|formula_dsl|formula_dialects` 之一与 `agg_type`，维度须含 `target_column`。
- 字典行四元组语义：`name`（业务权威名）/ `name_en+aliases`（合法别名，解析按此命中）/ `value_labels`（枚举码→业务标签）/ `description`（口径文字）。**同一概念的别名只登记在字典/对象层，不复制到别处。**
- 指标口径（formula）可能含物理列名：仅限服务端使用；对 LLM 与云端**只回文字性口径说明**（见护栏 §7 与 `to_cloud_md`）。

## 4. 引用一致性（级联校验，宁阻断勿悬空）
- 触碰对象 key 的任何变更（重命名/删除/合并），必须保证字典 `bound_object_key` 不悬空：
  - `save_draft`（草案）→ `find_orphan_dict_bindings` 告警不阻断（返回 `validation_warnings`）；
  - `activate` → 孤儿引用**硬阻断**（ValueError），不得为"先激活再修"开口子。
- 定期巡检：`scripts/check_alias_drift.py` 比对 字典 aliases / 图谱 skos:altLabel / business_terms 三处漂移，只报告不自动改。

## 5. 术语分层（business_terms 是缓冲区，不是第二事实源）
- `adh_business_terms` 只保留**尚未绑定到任何对象/字典的纯业务黑话**；一旦收编，其同义词应落对象/字典 aliases，术语行退役或改为溯源注释。
- 术语/同义词解析**必须按 datasource 分桶**（`terminology_manager.expand_synonyms(keywords, datasource_id)`）：ds>0 只加载本源+全局(ds=0)术语，杜绝跨数据源串味；ds=0 视为系统/全局调用加载全量。
- 新增术语消费方（检索、选表、解析）一律经 `terminology_manager` 缓存，不得各自 SELECT `adh_business_terms`。

## 6. 发布链路一致性（本体→图→云）
- 上云文档**只能**经 `ontology_service.to_cloud_md`（白名单渲染 + 版本戳头 `model-version-stamp`）由 `ontology_kb_sync` 推送；**严禁**直接把 `md_content`（含物理信息）上传。渲染→提取（`extract_object_keys`）、写戳→读戳（`_evaluate_doc_freshness`）均有跨层契约测试，改格式必须同步改两侧并跑契约测试。
- 同步成败必须落水位线表 `adh_ontology_kb_sync_state`（`_write_state`）；静默失败（只 warning）不允许——失败要可告警、可被 `reconcile()` 自愈。
- 知识库与字典的权威划分（写入 `prompt_composer.SEMANTIC_QUERY_RULES`，改动时不得删弱）：**名称以 `get_metrics` 实时目录为唯一权威；知识库只对口径解释/业务背景/模板 variables 权威；doc_stale=true 时 KB 内容仅供参考**。

## 7. 解析行为守则（确定性编译层）
- 名字解析优先级固定：字典精确名(name/name_en) → 字典别名 → `binding.column_map` → 物理列业务名(business_desc) → 物理列名兜底；每一档必须记录 `resolution_source` 入 provenance。
- **包含式模糊只回抛候选（`fuzzy_rejected` + `fuzzy_hint`），绝不静默绑定**——错数比无答案危害大（宁缺勿错）；被拒词自动落入 `adh_alias_suggestions`（见 FDE 规则）。
- 解析失败/未解析提示只允许出现业务名候选与字典清单，**不得含任何物理表/列名**（有 no_leak 用例把门）。

## 8. 建模回归门禁
触碰 `ontology_service.py` / `ontology_kb_sync.py` / `shared/semantics/{planner,models,intent,binding_resolver}.py` / `terminology_manager.py` 后必须跑：
- `venv/bin/python -m services.shared.eval.runner`（golden-question，**正确率不得回退**；`--suite retrieval` 另跑本体层检索评测）；
- `tests/test_cloud_md_redaction.py`、`tests/test_doc_freshness.py`、`tests/test_alias_suggestions.py`、`tests/test_terminology_scoping.py`、`tests/test_planner_resolution.py`；
- 涉及取数执行链（planner/semantic_query/gates）另按护栏 §11 跑护城河三件套。
新增建模/解析行为分支必须同步新增 eval 用例（尤其：别名命中档位、孤儿引用阻断、上云脱敏、模糊回抛）。
