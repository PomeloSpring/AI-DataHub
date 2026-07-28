"""Agent Pipeline Constants — shared across agent modules.

Contains:
- Iteration and context limits
- Tool result truncation budgets
- System tool definitions (Anthropic tool_use format)
"""

# ── Iteration & Context Limits ───────────────────────────────────────

MAX_ITERATIONS = 20           # Max tool-use rounds per agent run
MAX_CONTEXT_TOKENS = 180_000  # Estimated token budget before auto-compact
COMPACT_KEEP_RECENT = 6       # Number of recent messages to keep verbatim

# ── Tool Result Truncation Budgets ───────────────────────────────────
# Per-tool max characters for tool results entering the conversation.
# None = no truncation (critical info).

TOOL_RESULT_MAX_CHARS = {
    "retrieve_metadata": None,    # Critical: full schema needed
    "execute_sql": 8000,          # ~50 rows
    "list_tables": 4000,          # ~20 entries
    "get_sample_data": None,      # Only 5 rows
    "search_columns": 6000,       # ~30 entries
    "generate_sql": None,         # Structured JSON
    "think": 800,                 # Compact thinking
    "search_business_terms": 4000,
    "search_relations": 4000,
    "explain_error": None,
    "ask_user": None,
    "get_sql_rules": None,
    "validate_sql": None,
    "load_analysis_skill": None,
}
DEFAULT_TOOL_RESULT_MAX = 6000

# ── System Tool Definitions (Anthropic tool_use format) ──────────────

SYSTEM_TOOLS = [
    # === Context Gathering ===
    {
        "name": "list_tables",
        "description": "【补充工具】按关键词模糊搜索数据表。仅当 select_tables 结果不理想时使用，用于补充发现 select_tables 可能遗漏的表。一次传多个关键词（如 ['用户', '公司', '设备']），每个关键词最多返回10条。不要反复调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "搜索关键词列表（如 ['用户', '公司', '设备']），按表名和注释模糊匹配，每个关键词最多返回10条",
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "select_tables",
        "description": "【首选工具】根据自然语言问题，使用 BM25+向量混合检索选出最相关的数据表。自动提取关键词、扩展同义词、融合稀疏+稠密排序。返回匹配的表名列表。处理数据查询问题时必须首先调用此工具，不要用 list_tables 替代。",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的自然语言问题（直接传入原始问题即可，工具会自动提取关键词）",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "search_columns",
        "description": "在所有表中搜索匹配关键词的字段名。相当于 Grep，用于跨表查找特定字段（如搜索哪些表有 'company_id' 字段）。当你不确定某个字段在哪个表中时使用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要搜索的字段名关键词（如 'company'、'user_id'、'设备'）",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "retrieve_metadata",
        "description": "一次性检索指定表的完整元数据：M-Schema表结构、ER图(表关联关系)、业务术语、SQL模板示例。返回的内容可直接作为 generate_sql 的 context 参数使用。只需调用一次，传入所有需要的表名。",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要检索元数据的表名列表（建议传入 select_tables 返回的全部表名）",
                },
                "question": {
                    "type": "string",
                    "description": "用户的原始问题（必填），用于语义检索 SQL 模板和业务术语",
                },
            },
            "required": ["table_names", "question"],
        },
    },
    {
        "name": "get_sample_data",
        "description": "预览表的样本数据（前 5 行），帮助理解数据的实际内容和格式。相当于 cat 文件。在生成复杂 SQL 前先查看数据样本，确认字段含义和数据分布。",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "要预览的表名",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只预览指定的列",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "search_business_terms",
        "description": "搜索业务术语库，查找与关键词匹配的术语定义、计算公式、对应字段。用于理解用户问题中的业务概念。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要搜索的业务关键词",
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "search_relations",
        "description": "搜索表之间的关联关系（JOIN关系）。传入表名列表，返回这些表与其他表之间的外键关联。用于理解多表查询时如何 JOIN。当问题涉及多个实体（如用户+公司、订单+商品）时，必须调用此工具确认关联关系。",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查询关联关系的表名列表",
                },
            },
            "required": ["table_names"],
        },
    },
    # === Analysis Skill Loading ===
    {
        "name": "load_analysis_skill",
        "description": "加载分析领域专用提示词。当用户问题涉及趋势分析、异常检测、留存分析、漏斗分析、流量分析、用户画像等专业分析场景时，先调用此工具加载对应的专业提示词，再按提示词指引执行分析。加载后，严格遵循返回的提示词中的执行流程和分析框架。",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "分析技能名称",
                },
            },
            "required": ["skill_name"],
        },
    },
    # === SQL Generation & Execution ===
    {
        "name": "generate_sql",
        "description": "根据用户问题和元数据上下文，调用 LLM 生成 SQL。返回 JSON：{\"success\":true,\"sql\":\"...\",\"tables\":[...],\"chart-type\":\"...\"} 或 {\"success\":false,\"message\":\"...\"}。必须先调用 retrieve_metadata 获取上下文，并通过 context 参数传入。",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的自然语言问题",
                },
                "context": {
                    "type": "string",
                    "description": "元数据上下文（必填），由 retrieve_metadata 返回的完整内容，包含表结构、关联关系、术语、SQL模板",
                },
            },
            "required": ["question", "context"],
        },
    },
    {
        "name": "get_sql_rules",
        "description": "获取当前数据源的 SQL 语法规则和约束。在手写 SQL 之前必须先调用此工具获取规则。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "validate_sql",
        "description": "校验 SQL 语句的语法和安全性，返回校验结果和修复建议。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "要校验的 SQL 语句",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "execute_sql",
        "description": "执行 SQL 查询并返回结果。支持 Doris、MySQL、Elasticsearch 数据源。返回查询结果（列名、行数据、耗时）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "要执行的 SQL 语句",
                },
                "query_type": {
                    "type": "string",
                    "enum": ["sql", "rest", "dsl"],
                    "description": "查询类型：sql（标准SQL）、rest（ES REST API）、dsl（ES DSL JSON）。默认 sql。",
                },
            },
            "required": ["sql"],
        },
    },
    # === Self-Correction ===
    {
        "name": "explain_error",
        "description": "分析 SQL 执行错误的原因并给出修复建议。当 execute_sql 失败时，调用此工具获取详细的错误分析和修正方案。",
        "input_schema": {
            "type": "object",
            "properties": {
                "error_message": {
                    "type": "string",
                    "description": "execute_sql 返回的错误信息",
                },
                "failed_sql": {
                    "type": "string",
                    "description": "执行失败的 SQL 语句",
                },
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SQL 涉及的表名（可选，用于检查表结构）",
                },
            },
            "required": ["error_message", "failed_sql"],
        },
    },
    # === Reasoning ===
    {
        "name": "think",
        "description": "进行深度思考和推理，不产生任何副作用。用于：分析复杂问题的查询策略、规划多步查询、验证 SQL 逻辑是否正确。调用此工具不会执行任何操作，只是让你有结构化思考的空间。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "你的思考内容",
                },
            },
            "required": ["thought"],
        },
    },
    # === User Interaction ===
    {
        "name": "ask_user",
        "description": "当你无法确定应该查询哪些表、使用哪些字段、或需要用户澄清问题时，调用此工具向用户提问。提供选项让用户点击选择，也可以不提供选项让用户自由输入。",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "向用户提出的问题",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "供用户选择的选项列表（可选）",
                },
            },
            "required": ["question"],
        },
    },
]
