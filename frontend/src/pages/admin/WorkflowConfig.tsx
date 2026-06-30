import { Badge } from '@/components/ui/badge';
import { Zap, Workflow, Bot, Search, Database, Brain, Code, PlayCircle, BarChart3, GitBranch, Layers } from 'lucide-react';

// ── Mode definitions ─────────────────────────────────────────────────

const MODES = {
  quick: {
    name: '快速模式',
    icon: Zap,
    color: 'text-yellow-500',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/20',
    description: '简化 RAG 检索，响应快，适合简单查询',
    steps: [
      { name: '意图识别', icon: Brain, desc: '快速分类用户意图' },
      { name: '表选择', icon: Database, desc: '关键词 + 向量匹配选择相关表' },
      { name: '元数据检索', icon: Search, desc: 'HNSW 向量检索表结构和字段' },
      { name: 'SQL生成', icon: Code, desc: 'LLM生成SQL语句' },
      { name: 'SQL执行', icon: PlayCircle, desc: '执行查询返回结果' },
    ],
    features: [
      '响应速度快（通常 < 5秒）',
      '使用 HNSW 向量检索（768维 BGE-small-zh）',
      '适合单表简单查询',
      '无 Loop 自修复机制',
    ],
    limitations: [
      '复杂查询可能失败',
      '不支持多表关联优化',
      '无元数据补充机制',
    ],
  },
  deep: {
    name: '深度模式',
    icon: Workflow,
    color: 'text-blue-500',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/20',
    description: '完整 RAG + Loop 自修复，适合复杂问题',
    steps: [
      { name: '元数据检索', icon: Search, desc: '完整 HNSW 向量检索' },
      { name: 'LLM分析', icon: Brain, desc: '分析元数据是否充足' },
      { name: '元数据补充', icon: Database, desc: 'Loop循环：按需补充缺失的表/字段' },
      { name: 'SQL生成', icon: Code, desc: '基于完整元数据生成SQL' },
      { name: 'SQL执行', icon: PlayCircle, desc: '执行查询' },
      { name: '结果分析', icon: BarChart3, desc: '分析结果并提供洞察' },
    ],
    features: [
      '完整 HNSW 向量检索（768维 BGE-small-zh）',
      'Loop 自修复：元数据不足时自动补充（最多3轮）',
      '支持多表关联复杂查询',
      '结果二次分析',
    ],
    limitations: [
      '响应较慢（通常 10-30秒）',
      '不支持外部工具调用',
    ],
  },
  agent: {
    name: 'Agent模式',
    icon: Bot,
    color: 'text-purple-500',
    bgColor: 'bg-purple-500/10',
    borderColor: 'border-purple-500/20',
    description: 'LLM 自主决策，可调用 MCP 工具和外部 Agent',
    steps: [
      { name: '意图路由', icon: GitBranch, desc: 'LLM决定使用哪个Agent' },
      { name: '工具选择', icon: Layers, desc: '选择合适的工具（SQL/MCP/Agent）' },
      { name: '执行循环', icon: PlayCircle, desc: '自主执行多轮工具调用' },
      { name: '结果整合', icon: BarChart3, desc: '整合所有工具结果' },
    ],
    features: [
      'LLM 自主决策执行策略',
      '支持 MCP 工具调用（Redis、ES等）',
      '支持多 Agent 协作',
      '可通过 @mention 指定工具',
      '灵活应对复杂场景',
    ],
    limitations: [
      '响应时间不确定',
      '结果可能不稳定',
      '依赖外部服务可用性',
    ],
  },
};

// ── Retrieval strategies ─────────────────────────────────────────────

const RETRIEVAL_STRATEGIES = [
  {
    name: 'full_table',
    label: '全表检索',
    description: '默认策略，检索所有相关表的完整元数据',
    detail: '先通过关键词预选表，再使用 HNSW 向量检索匹配表结构、字段、术语和关联关系。适合大多数场景。',
    modes: ['quick', 'deep', 'agent'],
  },
  {
    name: 'column_first',
    label: '字段优先',
    description: '优先检索字段级元数据，再补充表级信息',
    detail: '先通过向量搜索匹配相关字段，再反查所属表的元数据。适合字段名明确的查询。',
    modes: ['quick', 'deep', 'agent'],
  },
  {
    name: 'two_stage',
    label: '两阶段检索',
    description: '第一阶段粗筛，第二阶段精排',
    detail: '第一阶段用关键词粗筛候选表，第二阶段用向量相似度精排并补充详细元数据。适合表数量较多的场景。',
    modes: ['deep', 'agent'],
  },
  {
    name: 'bidirectional',
    label: '双向检索',
    description: '正向（表→字段）+ 反向（字段→表）双向匹配',
    detail: '同时从表名和字段名两个方向进行向量检索，合并结果。适合不确定目标表的模糊查询。',
    modes: ['deep', 'agent'],
  },
];

// ── Flow Step Component ──────────────────────────────────────────────

function FlowStep({ step, isLast, modeColor }: {
  step: typeof MODES.quick.steps[0];
  isLast: boolean;
  modeColor: string;
}) {
  const Icon = step.icon;
  return (
    <div className="flex items-start gap-3">
      <div className="flex flex-col items-center">
        <div className={`w-10 h-10 rounded-full flex items-center justify-center ${modeColor} bg-background border-2`}>
          <Icon className="h-5 w-5" />
        </div>
        {!isLast && <div className="w-0.5 h-8 bg-border mt-1" />}
      </div>
      <div className="pt-2">
        <div className="font-medium text-sm">{step.name}</div>
        <div className="text-xs text-muted-foreground mt-0.5">{step.desc}</div>
      </div>
    </div>
  );
}

// ── Mode Card Component ──────────────────────────────────────────────

function ModeCard({ mode }: { mode: keyof typeof MODES }) {
  const config = MODES[mode];
  const Icon = config.icon;

  return (
    <div className={`rounded-lg border ${config.borderColor} p-5 space-y-4`}>
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${config.bgColor}`}>
          <Icon className={`h-5 w-5 ${config.color}`} />
        </div>
        <div>
          <h3 className="font-semibold text-lg">{config.name}</h3>
          <p className="text-sm text-muted-foreground">{config.description}</p>
        </div>
      </div>

      {/* Flow */}
      <div className={`${config.bgColor} rounded-lg p-4`}>
        <div className="text-xs font-medium text-muted-foreground mb-3">执行流程</div>
        <div className="space-y-0">
          {config.steps.map((step, i) => (
            <FlowStep
              key={step.name}
              step={step}
              isLast={i === config.steps.length - 1}
              modeColor={config.borderColor}
            />
          ))}
        </div>
      </div>

      {/* Features */}
      <div>
        <div className="text-xs font-medium text-muted-foreground mb-2">优势</div>
        <ul className="space-y-1">
          {config.features.map((f, i) => (
            <li key={i} className="text-sm flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              {f}
            </li>
          ))}
        </ul>
      </div>

      {/* Limitations */}
      <div>
        <div className="text-xs font-medium text-muted-foreground mb-2">局限</div>
        <ul className="space-y-1">
          {config.limitations.map((l, i) => (
            <li key={i} className="text-sm flex items-start gap-2">
              <span className="text-orange-500 mt-0.5">△</span>
              {l}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export default function WorkflowConfig() {
  return (
    <div className="h-full overflow-auto">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold">查询模式</h1>
          <p className="text-muted-foreground mt-1">
            AI-DataHub 提供三种查询模式，分别适用于不同复杂度的数据分析场景
          </p>
        </div>

        {/* Mode comparison cards */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <ModeCard mode="quick" />
          <ModeCard mode="deep" />
          <ModeCard mode="agent" />
        </div>

        {/* Retrieval strategies */}
        <div className="rounded-lg border p-5">
          <h2 className="text-lg font-semibold mb-1">元数据检索策略</h2>
          <p className="text-sm text-muted-foreground mb-4">
            不同模式支持不同的检索策略，用于从 RAG 知识库中匹配表结构和业务术语
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {RETRIEVAL_STRATEGIES.map(strategy => (
              <div key={strategy.name} className="rounded-lg border p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="font-medium">{strategy.label}</h3>
                  <div className="flex gap-1">
                    {strategy.modes.map(m => (
                      <Badge key={m} variant="outline" className="text-xs">
                        {m}
                      </Badge>
                    ))}
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">{strategy.description}</p>
                <p className="text-xs text-muted-foreground">{strategy.detail}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Mode selection guide */}
        <div className="rounded-lg border p-5">
          <h2 className="text-lg font-semibold mb-3">模式选择建议</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Zap className="h-4 w-4 text-yellow-500" />
                <span className="font-medium">选择快速模式</span>
              </div>
              <ul className="text-muted-foreground space-y-1 pl-6">
                <li>• 单表简单查询</li>
                <li>• 需要快速响应</li>
                <li>• 日常数据查看</li>
              </ul>
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Workflow className="h-4 w-4 text-blue-500" />
                <span className="font-medium">选择深度模式</span>
              </div>
              <ul className="text-muted-foreground space-y-1 pl-6">
                <li>• 多表关联查询</li>
                <li>• 复杂业务逻辑</li>
                <li>• 需要结果分析</li>
              </ul>
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Bot className="h-4 w-4 text-purple-500" />
                <span className="font-medium">选择Agent模式</span>
              </div>
              <ul className="text-muted-foreground space-y-1 pl-6">
                <li>• 需要外部数据源</li>
                <li>• 调用 Redis/ES 等工具</li>
                <li>• 复杂分析任务</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
