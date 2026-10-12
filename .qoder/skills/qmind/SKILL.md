---
name: qmind
description: 调用 Qoder QMind 云端知识库（notebook）的检索与运维技能：封装 CLI 认证（QMIND_TOKEN 从 backend/.env 自动读取）、notebook 列表、知识检索、常见故障排障。当需要读取或挖掘云端知识库（如 cloud-platform-data-wiki、AI-DataHub 系统知识库）、用户提到 qmind / QMind / 云端知识库 / notebook 检索、或 qmind CLI 报 AUTH_REQUIRED 时使用。
---

# QMind 云端知识库调用

## 快速开始（推荐：用封装脚本，环境/凭据已内置）

```bash
# 列出全部 notebook（notebook_id / title / description）
venv/bin/python .qoder/skills/qmind/scripts/qmind_query.py list

# 检索某个 notebook（--nb 传 notebook_id；问题用业务语言）
venv/bin/python .qoder/skills/qmind/scripts/qmind_query.py retrieve \
  --nb 01a11ade-23bb-737a-8207-8f0e0e40b437 -q "数据源与站点的对应关系" --top-k 8
# --nb 传注册表整数 id（adh_knowledge_bases.id）则走平台绑定检索路径
```

脚本负责：从 `backend/.env` 读 `QODER_PERSONAL_ACCESS_TOKEN`（pt- 令牌）注入 `QMIND_TOKEN`、
收敛 `QMIND_HOME=runtime/qmind/home`、走项目内置 `runtime/node` 驱动 CLI。**不要手动管理凭据**。

## 已知知识库（notebook_id）

| title | notebook_id | 用途 |
|---|---|---|
| cloud-platform-data-wiki | `01a11ade-23bb-737a-8207-8f0e0e40b437` | 云平台数据 wiki（数据源/站点/数据模型语义） |
| AI-DataHub 系统知识库 | `01a11aa4-210c-7277-b671-b67ceb3f6bf8` | 系统自身能力文档（本体/元数据/护城河） |
| cloud-platform-chatbi | `01a10253-9a98-7c77-a438-216b7d4f92c4` | Doris stardb 分析表本体建模 |

平台侧注册表是 `adh_knowledge_bases`（`kb_type='qmind'`，`qmind_notebook_id` 列对应上表）；
notebook 新建后需在注册表登记才被 AS-BOT `knowledge_search` 绑定使用。

## 直接调 CLI（脚本覆盖不到的命令才用）

```bash
export QMIND_TOKEN=$(grep -E '^\s*QODER_PERSONAL_ACCESS_TOKEN\s*=' backend/.env | cut -d= -f2- | tr -d ' \r')
export QMIND_HOME="$PWD/runtime/qmind/home" QMIND_NON_INTERACTIVE=1
# CLI 由 backend/modules/mind/rag/qmind_retriever.py 的 _cli_env/_cli_bin 解析（runtime/qmind-cli + runtime/node）
qmind notebook list --format json
qmind retrieve --nb <notebook_id> -q "<question>" --format json
```

## 检索策略

- **一次一问**：`qmind retrieve` 语义匹配整段 query，复合问题拆成多条分别检索再合并。
- 问业务概念用**用户会说的词**（如"高价值客户"），不要用物理表名——命中的是文档口径。
- 云端命中后要引用出处（文档标题/章节）；`rag_source` 应标 `qmind_hit`。
- 熔断/超时（CLI 默认 60s）时平台侧会显式降级（`degraded`/`qmind_circuit_open`），
  **不得把降级结果当正常命中**（no-silent-degradation）。

## 故障排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `AUTH_REQUIRED: no credentials found` | `QMIND_TOKEN` 未注入或令牌失效 | 核对 backend/.env 的 `QODER_PERSONAL_ACCESS_TOKEN`；pt- 令牌可换短 job token，过期需用户重新生成 |
| CLI 找不到 / 版本不对 | npm 缓存缺失 | 缓存在 `runtime/qmind-cli/`，由 `runtime/node` 驱动；勿用宿主机 node/qmind |
| 列表为空/检索空 | notebook 未授权给该令牌，或查询词与文档无关 | 先 `list` 确认可见；换业务词重试 |
| 大量 `qmind retrieve` 慢 | 每次 spawn CLI 子进程 | 平台侧有 Redis 熔断（连续 3 败熔 120s）；批量问题合并为一次调用 |

## 安全红线

- **令牌严禁回显/写入日志/存入任何文件或记忆**；输出只呈现 `pt-***`。
- 不要把 notebook 返回的整段原文直接贴给用户以外的系统；引用需带出处。
