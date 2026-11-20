# 本体模型构建格式 v1（设计评审稿）

状态：**设计评审稿 v1**（未落代码，等拍板）　日期：2026-11-20
范围边界：只规范「一份本体模型文档由哪些块组成、每块写什么、谁来消费」。
**不改** canonical JSON 的存储位置与编辑路径（仍是 `adh_ontology_models.json_content` 单一事实源 +
`ontology_service.save_draft/activate` 级联），**不新增**权限口径与执行把关点。

---

## 0. 一句话主张

> 一份模型 = **1 段描述** + **6 类结构化构件**（属性 / 关系 / 业务行为 / 动作 / 规则 / 场景）
> + **1 类内嵌结构**（场景里的「路径 path」，有序步骤口径，现行已有）
> + **2 类引用**（指标 / 维度，口径权威在字典）+ **1 个落地块**（部署站点与数据源）。

原则：**描述性内容只回答"是什么"**；凡"能发生什么、能做什么、必须满足什么、要回答什么问题"
一律进结构化块，可校验、可检索、可渲染，不留在散文里。

---

## 1. 六类构件的边界（最容易混的地方）

| 构件 | 回答的问题 | 可执行？ | 有参数？ | 现行键 | 权威落点 |
|---|---|---|---|---|---|
| **描述 Description** | 这个对象/模型是什么、覆盖什么业务 | 否 | 否 | `doc.description`、`objects[].description` | 本文档 |
| **属性 Property** | 对象有哪些可描述的字段值 | 否 | 否 | `objects[].properties[]` | 本文档（物理列名 `column` 仅内部） |
| **关系 Link** | 对象之间怎么连、基数多少、经什么桥 | 否 | 否 | `objects[].links[]` | 本文档（`join` 只进内部 md/图谱） |
| **行为 Behavior** | 业务上**会发生什么**（事件、状态迁移、生命周期后果） | 否 | 否 | **新增** `objects[].behaviors[]` | 本文档 |
| **动作 Action** | 系统里**能做的一次操作**（谁调、改什么、影响面） | 是 | 是 | `objects[].actions[]` | 本文档；**执行把关不在这里**（见 §6） |
| **规则 Rule** | 跨对象的口径 / 约束 / 质量 / 时效门禁 | 否（被引用） | 否 | `doc.rules[]` | 本文档 |
| **场景 Scenario** | 要回答的业务问题，串起对象+行为+动作+规则 | 否 | 否 | `doc.scenarios[]` | 本文档（与 `adh_knowledge_items` 的分工见 §7.2） |
| 指标 Metric | 这个对象能算哪些数、口径是什么 | 否 | 否 | `objects[].metrics[]`（**只是引用字符串**） | `adh_metrics`（`bound_object_key`） |
| **路径 Path** | 一串事件按什么顺序、在多久之内发生（漏斗/流转量） | 否（可编译为模板） | 否 | `doc.scenarios[].path`（**现行已有**） | 本文档（见 §2.7） |
| 维度 Dimension | 这个对象能按什么切 | 否 | 否 | 引用 | `adh_dimensions`（`bound_object_key`） |
| **落地 Deployment** | 哪些站点可查、路由到哪个数据源 | — | — | 列字段 `deploy_sites` / `object_site_rules` | **模型级列，不在 doc 正文里** |

### 关键分界：行为 ≠ 动作

| | 行为 Behavior | 动作 Action |
|---|---|---|
| 主语 | 业务世界（订单、设备、患者） | 平台/助手（人或 AS-BOT） |
| 句式 | 「订单被接单后进入生产中」 | 「生成本体草案（只产草案，不激活）」 |
| 有入参 | 无 | 有（`params_schema`） |
| 有后果 | 状态变化、可观测事件 | 写库、影响下游、风险级 |
| 建模价值 | 让模型/人懂**业务怎么流转**，判断"该看哪个状态" | 让 AS-BOT 知道**能做什么、不能做什么** |

现状问题：两者挤在 `actions` 一个键里，靠 `effect` / `read_only` / `risk` 混着表达 ——
业务流转（"设备离线后 7 天自动归档"）无处登记，建模者只能塞进 `description` 散文，
于是**检索不到、图谱里没有、体检也查不出缺失**。这就是本格式要拆开的核心理由。

> ⚠️ **撞名风险（必须先处理再落地）**：现行代码把 `actions[]` 叫作「行为」——
> `find_m234_issues` 的注释是"M2/M3/M4 建模位全部校验（**行为**/规则/场景/路径）"，而它调的是
> `validate_actions`；`to_cloud_md` 渲染的分节标题也是「行为(可执行动作)」。
> 本格式新增的 `behaviors[]` 若不改掉旧称呼，会在上云文档、体检报告、前端页签三处出现
> 两个"行为"（一个指可执行动作、一个指业务流转）。落地时统一改为：
> `actions` → 「动作（可执行）」，`behaviors` → 「业务行为（流转/状态迁移）」。

---

## 2. 各块字段规范（必填 / 可选 / 校验档位）

校验档位沿用现行两档：**error** = 激活硬阻断（`ontology_service.activate` 前 `find_m234_issues` /
`find_orphan_dict_bindings`）；**warning** = 草案返回 `validation_warnings` 不阻断。

### 2.1 属性 Property（`objects[].properties[]`，现行键不动）

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | **业务中文名**。无业务名不渲染（物理列名即泄露源，`ontology-modeling §2`） |
| `column` | ✅ | 物理落点 `表.列`，仅服务端与内部 md 使用；上云剔除 |
| `type` | ✅ | 逻辑类型（`int/string/datetime/decimal/bool`…），不是引擎原生类型串 |
| `is_key` | 可选 | 主键标记（图谱与选表用） |
| `description` | 建议 | 一句话口径；空只出 warning |
| `enum` | 可选 | **只读投影**：枚举唯一编辑源是维度字典 `value_labels`，此处由 `sync_enums_to_dimensions` 单向联动，不得手改 |
| `unit` / `format` | 可选（新增建议） | 展示单位与格式（`%`、`次`、`yyyy-MM-dd`），当前散在描述里 |

### 2.2 关系 Link（`objects[].links[]`，现行键不动）

| 字段 | 必填 | 说明 |
|---|---|---|
| `target` | ✅ | 目标对象 `key`，**不得悬空**（error） |
| `type` | ✅ | 关系语义名（图谱边 label：`declares/memberships/contains/produces…`） |
| `cardinality` | ✅ | `1:1 / 1:N / M:N`；缺了模型无法判断会不会放大行数 |
| `via` | 可选 | 桥对象 key（M:N 必须写，否则 join 路径只能靠猜） |
| `join` | 服务端 | 物理 JOIN 表达式，**只进内部 md / 图谱**，`to_cloud_md` 白名单剔除 |
| `description` | 建议 | 「用户通过 Membership 关联企业」这类业务解释 |

### 2.3 行为 Behavior（**新增** `objects[].behaviors[]`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | 对象内唯一（`{object_key}.{event}`），IRI/引用用 |
| `label` | ✅ | 业务名（「订单被接单」「设备判定离线」） |
| `trigger` | ✅ | 触发条件：事件或状态进入（自然语言 + 可选 `when` 结构化条件） |
| `from_state` / `to_state` | 状态迁移类必填 | 取值必须落在该对象状态属性的 `enum` 内（否则 error：状态越界） |
| `effect` | ✅ | 业务后果（「进入生产中，开始计 SLA」） |
| `observable_by` | 可选 | 该行为在数据里靠哪个属性/指标看出来（如 `order_status`、`device_online_days`）——**这是 ChatBI 能不能答"发生过多少"的关键** |
| `description` | 可选 | 补充说明 |

> 校验：`observable_by` 缺失只出 warning（表示"这条业务流转当前不可量化"，可进建模体检待办）。

### 2.4 动作 Action（`objects[].actions[]`，现行键保留 + 三项建议）

> 现行校验：`validate_actions`（`ontology_service.py:3014`）。下表「档位」列一律照实现写，不拿建议冒充已存。〔注：代码注释里这一块的中文名叫「行为」，见 §1 撞名注〕

| 字段 | 必填 | 现行档位 | 说明 |
|---|---|---|---|
| `key` | ✅ | **error**（缺失/全局重复） | 全局动作 key（`ontology.generate`）；注释称它是"审批通道注册键"——旧概念残留，落地时须改写 |
| `label` | 建议 | warn | 对外展示业务名 |
| `effect` | 建议 | 未校 | 一句话后果（「只产草案，不激活」）；**本格式要求必填** |
| `read_only` | ✅ | 参与分支判定 | 是否只读；决定后续 risk 是否被要求 |
| `risk` | 写动作必填 | 取值不合法=**error**；写动作缺失=warn | 枚举已钉死 `ACTION_RISKS = {low, medium, high}` |
| `params_schema` | 可选 | 非对象=**error** | 入参结构；**上云剔除**（`to_cloud_md` 只渲 `label`/`effect`） |
| `requires_approval` | 写动作建议 | warn（未声明时提醒） | 字段确实被校验器提醒，但**运行时无审批回路**（`as-bot-system-waker §3`）。建议废弃→必须同批改 `validate_actions` 的两条 warn，否则存草案永远出噪声；审批语义若真要建模，现存正位是规则的 `enforcement.level=require_approval` |
| `perm_code` | **新增建议** | 无 | 声明"这动作对应哪个权限码"，与 `perm_link` 登记做**同源校验**（只出 warn）；**不参与执行判决**（见 §6） |
| `mutates` | **新增建议** | 无 | 影响面对象/属性清单，供影响分析与场景引用 |

### 2.5 规则 Rule（`doc.rules[]`，现行键不动）

> 现行校验：`validate_rules`（`:3056`）。注意 `enforcement` **不是可选项**，缺了直接 error；`type` 也**已经是枚举**，不是"建议定枚举"。

| 字段 | 必填 | 现行档位 | 说明 |
|---|---|---|---|
| `key` | ✅ | **error**（缺失/重复） | 规则唯一键，场景经 `uses_rules` 引用 |
| `type` | ✅ | 不在枚举=**error** | 现行枚举 `RULE_TYPES = {permission, metric_constraint, business_constraint, quality}`。**本格式不自创枚举**：若要新增 `semantics 口径 / timeliness 时效`，属于破坏性变更，需同改 `RULE_TYPES` + 存量迁移 + `to_cloud_md` 分档文案，进 §8 拍板项 |
| `statement` | 建议 | warn | 必须是**可判定的一句话**（不是背景介绍） |
| `enforcement` | ✅ | 非对象=**error** | 执行语义/投影依据；`type=permission` 时三个子字段全部 **error** 级：`level` ∈ `{allow, require_approval, deny}`、`subject`（授权给谁，如 `role:admin`）、`action`（指向哪个动作 key 或 `*`；悬空即 error） |
| `source` | 建议 | 未校 | 规则出处（引 `.qoder/rules/*.md` 或需求编号），保证可追溯；**本格式要求必填** |
| `scope` | **新增建议** | 无 | 作用对象/属性 key 列表。现行只有权限类规则的 `enforcement.subject/action` 承担归属，**口径/质量/约束三类规则没有任何归属字段**，体检查不出"规则没落到任何对象" |

### 2.6 场景 Scenario（`doc.scenarios[]`，现行键不动 + 验收口径）

> 现行校验：`validate_scenarios`（`:3105`）——它只校 **key 唯一 + 三类引用不悬空**；`goal/actors/eval_seed` 现在**无人校**，本格式把它们从"建议"升级为"必填约定"（先靠评审，不进 error）。

| 字段 | 必填 | 现行档位 | 说明 |
|---|---|---|---|
| `key` | ✅ | **error**（缺失/重复） | 场景唯一键 |
| `title` | 建议 | warn | 业务标题 |
| `goal` | 本格式必填 | 未校 | 写成"回答谁的什么问题" |
| `actors` | 本格式必填 | 未校 | `role:admin` 之类，与权限登记同源 |
| `uses_objects` / `uses_actions` / `uses_rules` | ✅ | 引用悬空=**error** | 比对对象/动作/规则 key 集合（不区分大小写） |
| `path` | 可选 | 结构不合法=**error** | 有序步骤口径（漏斗/路径），单独见 §2.7 |
| `uses_behaviors` | **新增** | 无→建议纳入悬空校验 | 场景涉及的业务流转（本格式新增后配套） |
| `eval_seed` | 本格式必填 | 未校 | 问句种子，进 golden-question 评测 |
| `answer_shape` | **新增建议** | 无 | 期望产出形态（趋势/对比/明细/清单/结论建议），让评测有可判据、不只看"答得像" |

### 2.7 行为路径 Path（`scenarios[].path`，现行已有、本格式只补说法）

这一块很容易漏，但它是**现存第七类构件**（`validate_paths`，`:3137`）。它的定位要说清：

> **路径不是关系，是有序步骤口径。** 语义层只能编译单层聚合，漏斗类"按实体判序 → 再去重计数"靠模板对象承载，
> 所以路径必须声明得可确定解析，否则宁拒勿猜（现行就是 error）。

| 字段 | 现行档位 | 说明 |
|---|---|---|
| `key` | 缺失/重复=**error** | 可省，默认取所在场景 key；重复会让检索命中歧义 |
| `entity` / `event_time` | 写了但为空=**error** | 按什么实体串序、看什么时间（只认**业务名**，不得填物理列） |
| `window_sec` | ≤0/非整数=**error** | 步序时间窗 |
| `steps[]` | 结构不合法=**error** | 有序步骤清单；与 `behaviors[]` 天然对应（一条行为≈一步），落地后建议 steps 改为**引用 behavior key**而不是重写文字 |

---

## 3. 完整示例（一个对象写满六块）

以**现行系统本体**（`adh_ontology_models.kind='system'`，50 对象）里的 `ontology_model` 为底，
`properties` / `links` / `actions` / `rules` / `scenarios` 均取自库中真实内容，
`behaviors` 为本格式新增块的示范写法：

```json
{
  "key": "ontology_model",
  "display_name": "本体模型",
  "aliases": ["本体", "语义模型", "ontology"],
  "description": "一个业务域的语义层发布单元：canonical JSON 文档为唯一可编辑事实源，激活后级联展开对象、重建图谱、同步知识库。",
  "primary_table": "adh_ontology_models",

  "properties": [
    { "name": "模型ID",   "column": "adh_ontology_models.id",           "type": "bigint", "is_key": true },
    { "name": "模型名称", "column": "adh_ontology_models.name",         "type": "string" },
    { "name": "本体域别", "column": "adh_ontology_models.kind",         "type": "string",
      "enum": ["business", "system", "source"] },
    { "name": "生效状态", "column": "adh_ontology_models.status",       "type": "string",
      "enum": ["draft", "active", "archived"] }
  ],

  "links": [
    { "target": "metric",  "type": "declares",    "cardinality": "1:N",
      "description": "模型声明该对象的指标口径（口径正文登记在字典）" },
    { "target": "dataset", "type": "materializes","cardinality": "1:N",
      "description": "对象落地为可复用语义型数据集，看板/大屏只挂数据集" }
  ],

  "behaviors": [
    { "key": "ontology_model.draft_saved",
      "label": "草案已保存",
      "trigger": "saveDraft 写入 json_content（status=draft）",
      "from_state": null, "to_state": "draft",
      "effect": "线上口径不受影响；派生视图（对象展开行/图谱/知识库）仍是旧版",
      "observable_by": ["status", "updated_at"] },

    { "key": "ontology_model.activated",
      "label": "模型已激活",
      "trigger": "activate 通过全部 error 级校验（引用不悬空、M234 无 error、站点白名单不越权）",
      "from_state": "draft", "to_state": "active",
      "effect": "级联生效：重展开 objects → 重建图谱 → 同步知识库；旧草案版本可回滚",
      "observable_by": ["status", "version"] },

    { "key": "ontology_model.stale_detected",
      "label": "上云文档过期",
      "trigger": "文档版本戳 model-version-stamp 与当前生效版本不一致（_evaluate_doc_freshness）",
      "effect": "知识库内容降级为仅供参考，检索结果标注 doc_stale=true",
      "observable_by": ["version"] }
  ],

  "actions": [
    { "key": "ontology.generate", "label": "生成本体草案",
      "effect": "按数据源元数据生成草案（只产草案，不激活）",
      "read_only": false, "risk": "medium",
      "perm_code": "ontology:save", "mutates": ["ontology_model.json_content"] },
    { "key": "ontology.activate", "label": "激活本体模型",
      "effect": "草案生效并级联重建派生层；error 级校验不过直接拒",
      "read_only": false, "risk": "high",
      "perm_code": "ontology:publish", "mutates": ["ontology_model.status", "ontology_object", "knowledge_item"] }
  ]
}
```

顶层两块（真实内容，`behaviors` 引用为本格式新增）：

```json
{
  "rules": [{
    "key": "perm_admin_full_by_role_perms",
    "type": "permission",
    "statement": "管理员按角色勾选的权限码可发起全部系统动作；敏感基线仍对所有人生效，不因任何规则弱化",
    "source": "as-bot-system-waker §3 / security-guardrails §3",
    "scope": ["ontology_model", "ontology_object"]
  }],
  "scenarios": [{
    "key": "ops_modeling_progress",
    "title": "建模进度巡检",
    "goal": "回答业务方「本体建到哪了、还缺什么」",
    "actors": ["role:admin"],
    "uses_objects": ["ontology_model", "dataset"],
    "uses_behaviors": ["ontology_model.draft_saved", "ontology_model.activated"],
    "uses_actions": ["ontology.generate"],
    "uses_rules": ["perm_admin_full_by_role_perms"],
    "eval_seed": ["本体建模进度如何？还有哪些表没有进本体？"],
    "answer_shape": "清单 + 缺口归因"
  }]
}
```

**读法**（这就是"更好理解"的收益）：想知道**是什么** → `description`；想知道**能查什么** → `properties` +
引用的指标/维度；想知道**业务怎么走** → `behaviors`；想知道**能做什么** → `actions`；
想知道**什么不能违反** → `rules`；想知道**这套建模为了回答什么问题** → `scenarios`。

---

## 4. 与现行格式的 diff

| 变化 | 内容 | 影响面 |
|---|---|---|
| **新增** | `objects[].behaviors[]`（§2.3） | 写入侧、`find_m234_issues`、`to_md`/`to_cloud_md`、RDF、图谱、前端页签 |
| **新增** | `scenarios[].uses_behaviors` / `answer_shape` | 引用校验 + 评测可判据 |
| **新增建议** | `actions[].perm_code` / `mutates`；`rules[].scope`；`properties[].unit/format`；`links[].via` | 与 `perm_link` 登记做同源校验；影响分析；体检可查"规则无归属" |
| **建议废弃** | `actions[].requires_approval` | 现行链路无审批回路，留着会反向教模型旧概念（与退役 token 同一处理原则） |
| **不动** | 顶层 `domain/description/kind/sources/objects/rules/scenarios`；`deploy_sites` / `object_site_rules` 仍是模型级列；指标/维度口径仍在字典 | 存储与编辑路径零变更，存量 5 个模型可原样读 |

---

## 5. 配套改动清单（**不做就会出现"存得进去、看不见/查不到"**）

`actions[]`（M2）/ `rules[]`（M3）/ `scenarios[]`（M4）今天就是前车之鉴：字段**存得进去**，但下游八处没接通（实测，非推测）：

| # | 消费点 | 代码位置 | 现状 | 本格式必须补 |
|---|---|---|---|---|
| 1 | 内部文档渲染 | `ontology_service._object_md`（`to_md`，前端「文档」档） | 只循环 `objects`，**不含 actions/rules/scenarios** | 加 behaviors，并与 `to_cloud_md` 统一成一套分节口径（现在两套） |
| 2 | 上云脱敏投影 | `ontology_service.to_cloud_md` | 已渲染「行为(可执行动作)/业务规则/典型使用场景」，白名单剥 `params_schema/enforcement/join` | 加 behaviors 分节；白名单同步（物理信息不出云） |
| 3 | 草案差异 | `ontology_service._obj_shape` / `_obj_delta`（第 1925 行起） | 指纹只比 `display_name/aliases/primary_table/properties/links` → 改动作/规则/场景后审阅页全 `unchanged` | 纳入 behaviors 与 M234 |
| 4 | 覆盖防护 | `_reject_destructive_overwrite` | 只护 `properties/links` → 整份 JSON 覆盖把 `rules/scenarios` 抹空**不会被拦** | 纳入 behaviors/rules/scenarios（宁阻断勿损毁） |
| 5 | 语义层业务投影 | `sdk_tools/scoped_metadata` | 只输出 `properties`，行为/规则不回馈给语义层 AS-BOT | 决定要不要给（§7.3），给则按业务名投影、不带物理列 |
| 6 | 图谱 | `common/rdf/ontology_to_rdf.py`（JSON→Turtle）+ `semhub/graph/graph_service.py:564`（`sem_types = ["Action","Metric","Dimension","Rule","Scenario"]`） | 已发 Action/Rule/Scenario 节点与 `adh:hasAction / governs / controls / uses*` 边 | 新增 `adh:Behavior` 类型 + `transitionsTo`/`triggeredBy` 边；`sem_types` 与前端 `NODE_CONFIG` 色板同步（`graph-visualization §2`） |
| 7 | 前端建模界面 | `pages/catalog/OntologyModeling.tsx`（页签现为 **行为/规则/场景** + 新建/编辑对象）、`lib/ontologyObjects.ts: showsM234()` | `showsM234()` **返 true 不隐页签**（写不写由权限码把门，设计如此）；但现页签里的「行为」其实是 `actions`（见 §1 撞名注） | 改名「动作（可执行）」+ 新增「业务行为」页签与状态迁移可视化；体检项加"行为不可量化/状态越界" |
| 8 | 存量迁移 | **6 个** active 模型（系统本体 50 对象 + 联耀业务 18 + 数仓 4 + 云平台日志 4 + ARMS RUM 1 + 应用使用事件 1 = **78** 对象） | 无 behaviors | 幂等迁移脚本（dry-run 默认）；不补则 `behaviors: []` + 体检出 warning，**不得**静默判成"该对象没有业务流转" |

---

## 6. 执行把关在哪里（为什么动作块不管权限）

**本格式不新增任何把关点**，这一节只是为了防止后来人误读 `actions[]` / `rules[]`。

| 层 | 是什么 | 谁消费 | 不消费什么 |
|---|---|---|---|
| 建模描述层 | `objects[].actions[]`、`doc.rules[]` | 人（建模页/文档档）、图谱、上云投影、AS-BOT 提示词里的"这个对象能做什么"叙述 | **不参与执行判决**（`as-bot-system-waker §3` 明文：canonical 动作与规则 permission 仅是建模描述，无运行时消费方） |
| 执行裁决层 | `adh_user_roles ⋈ adh_role_perms ⋈ adh_perm_registry.ai_access` + 代码层涉密硬上界 `perm_link.SECRET_BOUND_NONE_PERMS` | `backend/core/perm_link.py: require_write_perm`（**唯一**回路，fail-closed） | 不回读本体 doc 里的 `risk` / `read_only` / `requires_approval` |

因此本格式对 `perm_code` / `mutates` 的定位必须钉死：

1. `perm_code` 是**同源校验字段**，不是执行开关。用途是让体检能查出"模型里声明的动作在权限登记表里查无此码"（或反过来：登记了权限码，模型里却没有对应动作），二者漂移只出 **warning**，绝不阻断，也绝不因为 doc 里写了码就放行。
2. `mutates` 只服务**影响分析与场景引用**（改哪个对象的什么、被哪些 scenario 用到），不得当作写权限判断依据。
3. `risk` / `read_only` 是给人看的标注。**不得**新增任何"读 doc 里 risk=high 就走审批/二次确认"的逻辑 —— 那会造出第二套"谁能做什么"的判断，与 `as-bot-system-waker §3`、`data-sync-engineering §2` 直接冲突，且配置面（角色页）与建模面（本体）必然漂移。
4. 数据工程域（`sync:*` / `dag:*` / `udf:*`）的动作**一律不进本体 `actions[]`**：`ai_access` 必为 `none`，本体的动作清单对 AI 广告它们等于空承诺（角色页勾不到、工具不生成、裁决不通过）。

---

## 7. 派生层与消费边界（每块写完之后给谁看）

### 7.1 三层派生，一律单向

```
json_content（唯一可编辑事实源）
   ├─ to_md        → 内部文档档（前端「文档」）        含 column/join/params_schema/enforcement
   ├─ to_cloud_md   → 上云知识库（qmind / KB）          白名单剔除物理列、JOIN、入参 schema
   ├─ ontology_to_rdf → 图谱（UNIFIED）                 节点/边，供影响分析与可视化
   └─ 对象展开行 adh_ontology_objects                    供字典 bound_object_key 引用
```

- 任何一块（含新增的 `behaviors[]`）只要写了，就必须同时登记它在 **to_md / to_cloud_md / RDF** 三处的渲染去向；只写进 json 而三处都没渲染 = "存得进去、看不见"（§5 第 1/2/6 行就是现状病例）。
- **禁止**独立编辑派生层（`ontology-modeling §1`）。改 behaviors 只改 doc，走 `save_draft → activate` 级联。

### 7.2 与知识库（`adh_knowledge_items`）的分工

上云之后同一句话可能既在本体投影里、又在知识库文档里，权威划分沿用现行提示词口径（`prompt_composer` 的「双路权威划分」，`ontology-modeling §6`）：

| 问题 | 唯一权威 | 知识库的角色 |
|---|---|---|
| 对象/指标/维度的**合法名称** | `get_metrics` 实时目录（直读当前生效本体） | 不作名称依据；冲突时以目录为准并向用户说明新旧版本差异 |
| **口径解释 / 业务背景 / 模板 variables** | 知识库文档 | 权威 |
| `behaviors[]`（业务流转、状态迁移） | 本体（本格式落地后） | 只作人类可读的叙述载体；**不得**被当成"可执行清单" |

`doc_stale=true`（版本戳不匹配）时知识库内容仅供参考，取数口径仍以生效本体为准。

### 7.3 语义层业务投影（`sdk_tools/scoped_metadata`）该给哪几块

现状实测：该投影只输出 `properties`（`scoped_metadata.py:208-238`），`actions` / `rules` / `scenarios` 一律不回馈语义层 AS-BOT。本格式的结论：

| 块 | 是否进语义投影 | 理由 |
|---|---|---|
| `properties` | ✅ 已给 | 业务名 → 可问字段 |
| **`behaviors`** | ✅ **建议给，且只给最小摘要**（`label` + `from_state→to_state` + `observable_by` 的业务名） | 它直接决定 ChatBI 能不能答"多少单已进入生产中 / 离线超 7 天的设备有多少"——没有状态迁移语义，模型只能猜枚举值。投影必须按业务名，**不带物理列**（`waker-datasource-domain §3`） |
| `actions` | ❌ 不给 | AS-BOT 的能力面由**工具注册**决定（`list_platform_functions` / `run_platform_function`）。从本体再播一遍动作清单，就是广告调不到的入口 = 空承诺，与技能细则悬空同类的失效模式 |
| `rules` | ⚠️ 只给 `type=semantics`（口径类） | 约束/权限/时效类规则属治理与运维语义，不该在取数路径上被模型自行"遵守"或绕过 |
| `scenarios` | ❌ 不给 | 场景是建模验收与评测（golden-question）的输入，不是会话上下文 |

---

## 8. 需要你拍板的五个点（不拍就停在评审稿）

1. **`behaviors` 是否真要做成独立块**，还是先用 `properties[].enum` + `description` 凑（凑的代价：状态流转不可校验、图谱里没有、体检查不出缺失）。
2. **`actions[].requires_approval` 是否废弃**（现行链路权限码直执行，字段无消费方）。
3. `perm_code` / `mutates` 只做**同源校验 warning**，确认不升级成阻断、不参与执行判决。
4. §7.3 的语义投影范围：只给 `properties`（零改动），还是加上 `behaviors` 最小摘要（要碰 `scoped_metadata` + 补用例）。
5. §5 八处配套改动的**落地顺序**：建议 4（覆盖防护）→ 3（草案差异）→ 1/2（两套 md 渲染统一）→ 6（图谱）→ 7（前端页签）→ 8（存量迁移），把"存得进去但看不见"和"整份覆盖抹空 rules/scenarios 不拦"这两个真缺陷排在前面。
