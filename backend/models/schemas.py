"""Pydantic data models for ChatBI API."""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


# ── Auth ──
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str = ""
    token_type: str = "bearer"
    user: "UserInfo"

class UserInfo(BaseModel):
    id: int
    username: str
    role: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    email: Optional[str] = ""
    phone: Optional[str] = ""

class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class ResetPasswordRequest(BaseModel):
    new_password: str

class UpdateUserStatusRequest(BaseModel):
    status: str  # active / disabled

class UserProfile(BaseModel):
    id: int
    username: str
    role: str
    email: str = ""
    phone: str = ""
    avatar: str = ""
    status: str = "active"
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class UserListResponse(BaseModel):
    items: list[UserProfile]
    total: int

class AuditLogItem(BaseModel):
    id: int
    user_id: int
    username: str
    action: str
    target_type: str = ""
    target_id: int = 0
    detail: str = ""
    ip_address: str = ""
    created_at: datetime

class AuditLogResponse(BaseModel):
    items: list[AuditLogItem]
    total: int


# ── Chat ──
class ChatRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []  # Multi-turn conversation history
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None  # LLM model ID, None = default
    workflow_id: Optional[int] = None  # Workflow ID for Loop Engine, None = default
    pipeline_mode: Optional[str] = None  # Pipeline mode: "quick", "deep" (pipeline endpoint only)
    retrieval_strategy: Optional[str] = None  # RAG strategy: full_table, column_first, two_stage, bidirectional, graph
    mcp_tools: list[str] = []  # List of selected MCP tool names
    workspace_id: Optional[int] = 0  # Workspace ID for Agent mode

class ChatMessage(BaseModel):
    role: str
    content: str
    sql: Optional[str] = None
    warnings: Optional[list[str]] = None
    thinking: Optional[str] = None
    rag: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None

class ConfirmSqlRequest(BaseModel):
    sql: str
    question: str
    chart_type: Optional[str] = None
    datasource_id: Optional[int] = 0


# ── Query ──
class QueryRequest(BaseModel):
    sql: str

class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict]
    row_count: int
    elapsed_ms: int


# ── Dashboard ──
class ChartConfig(BaseModel):
    id: Optional[int] = None
    name: str
    chart_type: str
    sql_query: str
    config: Optional[dict] = None
    position: Optional[dict] = None
    source_type: Optional[str] = "query"
    source_id: Optional[int] = None
    data_cache: Optional[str] = None

class DashboardCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    layout: Optional[list[dict]] = []
    filters: Optional[dict] = {}
    params: Optional[list[dict]] = []
    status: Optional[str] = "designing"  # designing, enabled, closed
    is_public: bool = False
    is_default: bool = False
    carousel_interval: int = 0
    workspace_id: Optional[int] = 0

class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    layout: Optional[list[dict]] = None
    filters: Optional[dict] = None
    params: Optional[list[dict]] = None
    status: Optional[str] = None
    is_public: Optional[bool] = None
    is_default: Optional[bool] = None
    carousel_interval: Optional[int] = None

class DashboardResponse(BaseModel):
    id: int
    name: str
    description: str
    layout: list[dict]
    filters: dict
    owner_id: int
    is_public: bool
    is_default: bool
    carousel_interval: int
    charts: list[ChartConfig]
    created_at: datetime
    updated_at: datetime


# ── Chart Snapshot ──
class ChartSnapshot(BaseModel):
    id: int
    user_id: int
    question: str
    sql_query: str
    chart_type: str
    brief: str
    columns: str
    row_count: int
    created_at: datetime


# ── History ──
class HistoryItem(BaseModel):
    id: int
    username: str
    user_role: str
    question: str
    generated_sql: str
    execution_status: str
    row_count: int
    execution_time_ms: int
    error_message: str
    created_at: datetime


# ── Admin ──
class SyncResponse(BaseModel):
    success: bool
    message: str
    count: int = 0

class MetadataCreate(BaseModel):
    table_name: str
    column_name: str
    data_type: str = "VARCHAR"
    column_comment: Optional[str] = ""
    business_desc: Optional[str] = ""
    keywords: Optional[str] = ""
    is_key: Optional[str] = "false"
    is_nullable: Optional[str] = "true"
    is_active: bool = True
    datasource_id: Optional[int] = 0

class MetadataUpdate(BaseModel):
    column_comment: Optional[str] = None
    business_desc: Optional[str] = None
    keywords: Optional[str] = None
    is_active: Optional[bool] = None

class TableInfoCreate(BaseModel):
    table_name: str
    table_comment: Optional[str] = ""
    table_business_desc: Optional[str] = ""
    keywords: Optional[str] = ""
    domain_tag: Optional[str] = ""
    region_tag: Optional[str] = ""
    is_active: bool = True
    datasource_id: Optional[int] = 0

class TableInfoUpdate(BaseModel):
    table_comment: Optional[str] = None
    table_business_desc: Optional[str] = None
    keywords: Optional[str] = None
    domain_tag: Optional[str] = None
    region_tag: Optional[str] = None
    is_active: Optional[bool] = None

class TemplateCreate(BaseModel):
    template_id: str
    template_name: str
    category: str = ""
    intent_keywords: str = ""
    sql_template: str
    variables: Optional[str] = ""
    description: Optional[str] = ""
    rules: Optional[str] = ""
    is_active: bool = True
    datasource_id: int = 0

class TemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    category: Optional[str] = None
    intent_keywords: Optional[str] = None
    sql_template: Optional[str] = None
    variables: Optional[str] = None
    description: Optional[str] = None
    rules: Optional[str] = None
    is_active: Optional[bool] = None
    datasource_id: Optional[int] = None

class TermCreate(BaseModel):
    term_cn: str
    term_en: Optional[str] = ""
    term_aliases: Optional[str] = ""
    term_type: str = "dimension"
    target_table: Optional[str] = ""
    target_column: Optional[str] = ""
    calculation: Optional[str] = ""
    description: Optional[str] = ""

class TermUpdate(BaseModel):
    term_cn: Optional[str] = None
    term_en: Optional[str] = None
    term_aliases: Optional[str] = None
    term_type: Optional[str] = None
    target_table: Optional[str] = None
    target_column: Optional[str] = None
    calculation: Optional[str] = None
    description: Optional[str] = None


# ── Playground ──
class SavedQueryCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    sql_query: str
    is_dataset: bool = False
    dataset_keywords: Optional[str] = ""

class SavedQueryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sql_query: Optional[str] = None
    is_dataset: Optional[bool] = None
    dataset_keywords: Optional[str] = None

class SavedQueryResponse(BaseModel):
    id: int
    name: str
    description: str
    sql_query: str
    is_dataset: bool
    dataset_keywords: str
    owner_id: int
    created_at: datetime
    updated_at: datetime


# ── Datasource ──
class RelationCreate(BaseModel):
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relation_type: str = "1:N"
    join_type: str = "INNER"
    description: Optional[str] = ""
    is_active: bool = True
    datasource_id: Optional[int] = 0

class RelationUpdate(BaseModel):
    source_column: Optional[str] = None
    target_column: Optional[str] = None
    relation_type: Optional[str] = None
    join_type: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DatasourceCreate(BaseModel):
    name: str
    db_type: str = "mysql"
    host: str
    port: int = 3306
    username: str
    password: str
    database_name: Optional[str] = ""
    is_default: bool = False
    ssl: bool = False

class DatasourceUpdate(BaseModel):
    name: Optional[str] = None
    db_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database_name: Optional[str] = None
    is_default: Optional[bool] = None
    ssl: Optional[bool] = None


# ── Brand Settings ──
class BrandSettings(BaseModel):
    app_name: str = "ChatBI"
    logo_url: Optional[str] = ""  # Base64 data URL or external URL
    show_icon: bool = True
    show_text: bool = True

class BrandSettingsUpdate(BaseModel):
    app_name: Optional[str] = None
    logo_url: Optional[str] = None
    show_icon: Optional[bool] = None
    show_text: Optional[bool] = None


# ── Menu Schemas ──────────────────────────────────────────────────────

class MenuItemCreate(BaseModel):
    name: str
    icon: str = ""
    parent_id: Optional[int] = None
    page_id: Optional[int] = None
    link_type: str = "page"  # 'page' or 'screen'
    is_system: bool = False
    workspace_id: int = 0

class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    parent_id: Optional[int] = None
    page_id: Optional[int] = None
    link_type: Optional[str] = None
    sort_order: Optional[int] = None

class MenuItemResponse(BaseModel):
    id: int
    parent_id: Optional[int]
    name: str
    icon: str
    page_id: Optional[int]
    link_type: str = "page"
    is_system: bool
    sort_order: int
    children: list = []

class MenuTreeResponse(BaseModel):
    items: list[MenuItemResponse]


# ── Component Data Schemas ────────────────────────────────────────────

class ComponentDataOptions(BaseModel):
    page: Optional[int] = None
    size: Optional[int] = None
    sort_by: Optional[str] = None
    sort_order: Optional[str] = None
    agg_method: Optional[str] = None
    group_by: Optional[str] = None

class ComponentDataRequest(BaseModel):
    datasource_id: int
    sql: str
    params: dict = {}
    component_type: str = "table"
    options: Optional[ComponentDataOptions] = None

class ComponentDataResponse(BaseModel):
    success: bool
    data: list = []
    total: Optional[int] = None
    columns: list = []
    error: Optional[str] = None


# ── Embed / Integration ──────────────────────────────────────────

class EmbedVerifyRequest(BaseModel):
    api_key: str
    user_id: str
    user_name: Optional[str] = ""

class EmbedVerifyResponse(BaseModel):
    embed_token: str
    expires_at: datetime
    app_id: int

class EmbedRefreshRequest(BaseModel):
    embed_token: str

class ApplicationCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    enable_chat: bool = True
    allowed_dashboards: Optional[list[int]] = None
    allowed_tables: Optional[list[str]] = None
    rate_limit: int = 60

class ApplicationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enable_chat: Optional[bool] = None
    allowed_dashboards: Optional[list[int]] = None
    allowed_tables: Optional[list[str]] = None
    rate_limit: Optional[int] = None
    status: Optional[str] = None

class ApplicationResponse(BaseModel):
    id: int
    name: str
    status: str
    enable_chat: bool = True
    allowed_dashboards: Optional[str] = None
    allowed_tables: Optional[str] = None
    rate_limit: int
    description: str = ""
    last_used_at: Optional[datetime] = None
    created_by: int
    created_at: datetime
    updated_at: datetime

class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int

class ApplicationKeyResponse(BaseModel):
    id: int
    name: str
    api_key: str
    message: str

class EmbedLogItem(BaseModel):
    id: int
    app_id: int
    user_id: str
    user_name: str = ""
    action: str
    detail: str = ""
    ip_address: str = ""
    status: str = "success"
    error_message: str = ""
    created_at: datetime

class EmbedLogResponse(BaseModel):
    items: list[EmbedLogItem]
    total: int

class EmbedChatRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0

class EmbedDashboardListResponse(BaseModel):
    items: list[dict]
    total: int


# ── Loop Engineering: Prompt Management ─────────────────────────────

class PromptCreate(BaseModel):
    prompt_key: str
    prompt_name: str
    system_prompt: Optional[str] = ""
    user_prompt_template: Optional[str] = ""
    description: Optional[str] = ""
    change_log: Optional[str] = ""

class PromptUpdate(BaseModel):
    prompt_name: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    description: Optional[str] = None
    change_log: Optional[str] = None

class PromptResponse(BaseModel):
    id: int
    prompt_key: str
    prompt_name: str
    system_prompt: Optional[str] = ""
    user_prompt_template: Optional[str] = ""
    description: Optional[str] = ""
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = ""
    change_log: Optional[str] = ""

class PromptVersionResponse(BaseModel):
    id: int
    prompt_id: int
    prompt_key: str
    version: int
    system_prompt: Optional[str] = ""
    user_prompt_template: Optional[str] = ""
    change_log: Optional[str] = ""
    created_at: datetime
    created_by: Optional[str] = ""
    is_current: bool

class PromptListResponse(BaseModel):
    items: list[PromptResponse]
    total: int


# ── Loop Engineering: Workflow Config ───────────────────────────────

class WorkflowStepConfig(BaseModel):
    step_type: str  # metadata_retrieval/llm_analysis/metadata_supplement/sql_generation/sql_execution/result_analysis
    step_name: str
    step_order: int
    max_rounds: int = 1
    is_enabled: bool = True
    prompt_key: Optional[str] = None
    config: Optional[dict] = None

class WorkflowConfigCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    is_active: bool = True
    is_default: bool = False
    workflow_type: str = "linear"  # linear/dag
    dag_config: Optional[str] = None
    steps: Optional[list[WorkflowStepConfig]] = []
    edges: Optional[list["WorkflowEdgeCreate"]] = []

class WorkflowConfigUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    workflow_type: Optional[str] = None
    dag_config: Optional[str] = None
    steps: Optional[list[WorkflowStepConfig]] = None
    edges: Optional[list["WorkflowEdgeCreate"]] = None

class WorkflowStepUpdate(BaseModel):
    step_name: Optional[str] = None
    max_rounds: Optional[int] = None
    is_enabled: Optional[bool] = None
    prompt_key: Optional[str] = None
    config: Optional[dict] = None

class WorkflowStepResponse(BaseModel):
    id: int
    workflow_id: int
    step_type: str
    step_name: str
    step_order: int
    max_rounds: int
    is_enabled: bool
    prompt_key: Optional[str] = None
    config: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

class WorkflowConfigResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = ""
    is_active: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = ""
    steps: list[WorkflowStepResponse] = []

class WorkflowListResponse(BaseModel):
    items: list[WorkflowConfigResponse]
    total: int


# ── Loop Engineering: Workflow Execution Log ────────────────────────

class WorkflowLogResponse(BaseModel):
    id: int
    workflow_id: int
    workflow_name: Optional[str] = ""
    session_id: str
    user_id: Optional[int] = None
    username: Optional[str] = ""
    question: Optional[str] = ""
    current_step: Optional[str] = ""
    current_round: Optional[int] = 0
    metadata_context: Optional[str] = ""
    metadata_requested: Optional[str] = ""
    metadata_supplemented: Optional[str] = ""
    llm_analysis: Optional[str] = ""
    generated_sql: Optional[str] = ""
    execution_result: Optional[str] = ""
    analysis_result: Optional[str] = ""
    chart_type: Optional[str] = ""
    status: str = "running"
    error_message: Optional[str] = ""
    started_at: datetime
    completed_at: Optional[datetime] = None
    elapsed_ms: Optional[int] = 0

class WorkflowLogListResponse(BaseModel):
    items: list[WorkflowLogResponse]
    total: int


# ── Loop Engineering: Loop Execution Request ────────────────────────

class LoopExecutionRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    workflow_id: Optional[int] = None  # None = use default


# ── DAG Workflow Extensions ──────────────────────────────────────────

class WorkflowEdgeCreate(BaseModel):
    source_step_id: int
    target_step_id: int
    edge_type: str = "normal"  # normal/conditional/error
    condition_expr: Optional[str] = None
    label: Optional[str] = None

class WorkflowDAGConfig(BaseModel):
    name: str
    description: Optional[str] = ""
    is_active: bool = True
    is_default: bool = False
    workflow_type: str = "dag"  # linear/dag
    dag_config: Optional[str] = None
    steps: Optional[list[WorkflowStepConfig]] = []
    edges: Optional[list[WorkflowEdgeCreate]] = []


# ── Scheduled Tasks ────────────────────────────────────────────────

class ScheduledTaskCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    task_type: str  # query / agent
    task_config: dict  # SQL列表/Agent问题列表/数据源ID等
    report_template_key: Optional[str] = None
    cron_expression: str
    timezone: Optional[str] = "Asia/Shanghai"
    channel_id: Optional[int] = None
    notify_on_success: bool = True
    notify_on_failure: bool = True
    is_active: bool = True
    workspace_id: int = 0
    timeout_seconds: int = 300
    max_retries: int = 0

class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    task_config: Optional[dict] = None
    report_template_key: Optional[str] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    channel_id: Optional[int] = None
    notify_on_success: Optional[bool] = None
    notify_on_failure: Optional[bool] = None
    is_active: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None

class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    description: str
    task_type: str
    task_config: dict
    report_template_key: Optional[str] = None
    cron_expression: str
    timezone: str
    channel_id: Optional[int] = None
    notify_on_success: bool
    notify_on_failure: bool
    is_active: bool
    workspace_id: int
    owner_id: int
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    run_count: int
    timeout_seconds: int
    max_retries: int
    created_at: datetime
    updated_at: datetime

class ScheduledTaskListResponse(BaseModel):
    items: list[ScheduledTaskResponse]
    total: int


# ── Scheduled Task Logs ────────────────────────────────────────────

class ScheduledLogResponse(BaseModel):
    id: int
    scheduled_task_id: int
    workspace_id: int
    status: str
    trigger_type: str
    celery_task_id: Optional[str] = None
    result_summary: Optional[str] = None
    result_data: Optional[Any] = None
    error_message: Optional[str] = None
    questions_executed: Optional[list] = None
    questions_succeeded: int
    questions_failed: int
    report_content: Optional[str] = None
    channel_response: Optional[str] = None
    notify_status: Optional[str] = None
    elapsed_ms: Optional[int] = None
    token_usage: Optional[Any] = None
    worker_id: Optional[str] = None
    report_id: Optional[int] = None
    report_access_token: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    created_at: datetime

class ScheduledLogListResponse(BaseModel):
    items: list[ScheduledLogResponse]
    total: int


# ── Notification Channels ──────────────────────────────────────────

class NotificationChannelCreate(BaseModel):
    name: str
    channel_type: str  # dingtalk / feishu / wecom / email / webhook
    config: dict  # webhook URL、密钥、SMTP 配置等
    is_active: bool = True
    workspace_id: int = 0

class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = None
    channel_type: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None

class NotificationChannelResponse(BaseModel):
    id: int
    name: str
    channel_type: str
    config: dict
    is_active: bool
    workspace_id: int
    owner_id: int
    last_test_at: Optional[datetime] = None
    last_test_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ── Report Templates ───────────────────────────────────────────────

class ReportTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    content: str
    format: str = "markdown"  # markdown / html
    workspace_id: int = 0

class ReportTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    format: Optional[str] = None

class ReportTemplateResponse(BaseModel):
    id: int
    name: str
    description: str
    content: str
    format: str
    is_system: bool
    workspace_id: int
    owner_id: int
    created_at: datetime
    updated_at: datetime


# ── Generated Reports ─────────────────────────────────────────────

class ReportResponse(BaseModel):
    id: int
    task_id: int
    log_id: Optional[int] = None
    title: str
    content: str
    format: str
    access_mode: str
    workspace_id: int
    owner_id: int
    view_count: int
    created_at: datetime
