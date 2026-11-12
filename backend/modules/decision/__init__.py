"""Decision Advice module (决策建议) — 平台第三个能力面。

数据分析回答「发生了什么」，知识沉淀回答「为什么 / 标准怎么做」，本模块回答
**「该做什么」**：决策树是业务判断口径资产（一等公民），条件求值由确定性引擎完成，
LLM 不参与判断本身，只解释依据与下一步。

依赖方向（与 gov / viz 同构，严格单向）：
    api/  →  service.py / engine.py  →  dictionary.py  →  backend.common.db
    engine.py → backend.semantics.execute（唯一取数口，护栏 §1）

不变项（改动前必读，.qoder/rules/）：
  * 取数只经 `backend.semantics.execute`，不新增任何裸连通道；
  * 可查站点/数据源唯一裁决点是角色授权（resource_guard / site_service），
    本模块不持有数据源边界、不新建 adh_role_site_access 之类的第二套判断；
  * 权限口径唯一 = adh_role_perms ⋈ adh_perm_registry（经 api_permission 中间件按
    api_pattern 裁决），本模块不再写一套「谁能看建议」的判断；
  * 决策树 `tree_doc` 是唯一可编辑事实源，`required_metrics` / `bound_object_keys`
    是发布时重算的派生列（对齐 ontology-modeling §1）。
"""
