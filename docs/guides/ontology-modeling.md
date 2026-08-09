# 本体建模（Ontology Modeling）指南

本体建模以**业务对象**（而非物理表）为中心组织数据语义：页面触发 LLM 从表结构、业务术语、指标、关系中归纳本体草案，用户检查确认后激活，逐对象向量化写入向量库，供 `ontology_first` 检索策略使用。

参考范式：FDE（Forward Deployed Engineer）对象中心本体 —— ObjectType / Property / Link / Metric。

## 整体流程

```
选数据源 → [生成本体模型]（LLM 草案，SSE 进度）
  → 三格式检查/编辑（JSON 为主编辑面）
  → [确认激活] → 逐对象 MD 段向量化入 Doris HNSW
  → datamind 选择 ontology_first 策略检索
```

- 每个数据源至多一个 **active** 模型 + 一个 **draft**；激活时旧 active 自动归档。
- 人工确认为必经闸门，不做自动激活。

## 三格式约定

| 格式 | 角色 | 说明 |
|---|---|---|
| JSON | **唯一事实源** | 接口流转与编辑载体；保存时服务端重新派生 YAML/MD |
| YAML | 结构可读 | 派生产物，只读预览 |
| MD | 向量化文本 | 按对象分节，激活时逐段 embedding |

> 直接修改 MD 不会回写结构；仅 JSON 编辑生效。

### JSON Schema

```json
{
  "datasource_id": 1,
  "domain": "电商交易",
  "description": "该数据源覆盖订单、客户、商品的核心交易链路。",
  "objects": [
    {
      "key": "order",
      "display_name": "订单",
      "aliases": ["交易单", "订单表"],
      "description": "客户下单产生的交易记录，含支付与履约状态。",
      "primary_table": "dwd_order",
      "properties": [
        {
          "column": "dwd_order.order_id",
          "name": "订单号",
          "type": "BIGINT",
          "is_key": true,
          "description": "订单唯一标识",
          "enum": []
        },
        {
          "column": "dwd_order.status",
          "name": "订单状态",
          "type": "TINYINT",
          "is_key": false,
          "description": "订单当前状态",
          "enum": ["0=待支付", "1=已支付", "2=已取消"]
        }
      ],
      "links": [
        {
          "target": "customer",
          "type": "belongs_to",
          "join": "dwd_order.customer_id = dim_customer.customer_id",
          "cardinality": "N:1",
          "description": "订单归属客户"
        }
      ],
      "metrics": [
        { "name": "GMV", "formula": "SUM(pay_amount)", "description": "成交总额" }
      ]
    }
  ]
}
```

### MD 分节模板（向量化文本）

每个对象一段，激活时逐段 embedding：

```markdown
## 业务对象: 订单 (order)
别名: 交易单、订单表
描述: 客户下单产生的交易记录，含支付与履约状态。
主表: dwd_order

### 属性
- dwd_order.order_id (BIGINT, 主键): 订单号，订单唯一标识
- dwd_order.status (TINYINT): 订单状态，订单当前状态；枚举: 0=待支付; 1=已支付; 2=已取消

### 关系
- belongs_to customer，join: dwd_order.customer_id = dim_customer.customer_id (N:1)：订单归属客户

### 指标
- GMV = SUM(pay_amount)（成交总额）
```

## 页面操作

入口：数据中台 → 数据目录 → **本体建模**（路由 `/data/ontology`）。

1. **选择数据源**：生成依赖该数据源已同步的元数据（`adh_table_info` / `adh_column_metadata` / `adh_business_terms` / `adh_table_relations` 及可选指标）。
2. **生成本体模型**：SSE 流式返回进度；大库按 `domain_tag` 分批（每批 ≤20 表）归纳后合并去重；重新生成会替换该数据源已有 draft。
3. **编辑**：仅 draft 可编辑。修改 JSON 后「保存草案」，服务端校验 JSON 并重派生 YAML/MD。
4. **确认激活**：二次确认弹窗提示将重建向量；激活后对象写入 `adh_ontology_objects`（embedding 非空），原 active 模型转 archived。
5. **右侧对象预览**：实时解析 JSON，展示对象名/别名/属性数/关系数/指标数/主表。

## API

挂载在 `/api/catalog/ontology`：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/generate` | SSE 流式生成草案（event: progress / done / error） |
| GET | `/models` | 模型列表（可按 `datasource_id` 筛选） |
| GET | `/models/{id}` | 模型详情（含三格式内容） |
| PUT | `/models/{id}` | 保存草案（body: `json_content`，可选 `name`） |
| POST | `/models/{id}/activate` | 激活并向量化 |
| POST | `/models/{id}/archive` | 归档并下线对象向量 |
| DELETE | `/models/{id}` | 删除模型 |
| GET | `/search?q=&datasource_id=&limit=` | 对象向量检索（调试/预览） |

## ontology_first 检索策略

datamind 侧 `services/datamind/rag/strategies/ontology.py`：

1. 问题向量化 → 在 `adh_ontology_objects`（active 对象）做 Doris ANN 召回 top-k 对象
2. 从所属模型 JSON 展开物理元数据：主表 + 属性列前缀表 + 链接 join 涉及的表
3. 并行装填术语/模板/关系/数据集，输出标准 RAG result dict（`prompt_builder` 及下游零改动）
4. **回退行为**：无 active 模型或召回为空时自动回退 hybrid，`rag_source` 标记为 `ontology_first:fallback:...`

启用方式：datamind 检索策略选择器中选择 `ontology_first`（`STRATEGY_CHOICES` 自动带出）。

## 数据表

DDL 位于 `sync/create_ontology_tables.sql`：

- `adh_ontology_models`：模型主表（三格式 TEXT 内容 + 状态机 draft/active/archived）
- `adh_ontology_objects`：对象向量表（`md_section` 为向量化文本，`embedding ARRAY<FLOAT>` + HNSW 索引，768 维 / l2_distance）

properties / links / metrics 不单独建表，存于 `json_content`，检索时服务端解析，保持轻量。

## 常见问题

- **重新激活是否增量更新向量？** 否，激活即全量重建该模型对象向量（对象数通常 <200，成本可接受）。
- **生成质量不佳？** 草案质量依赖已有元数据丰富度；先补全表业务描述、术语与关系，再重新生成。
- **激活后检索仍走旧逻辑？** 确认 datamind 检索策略已切换为 `ontology_first`。
