/**
 * 「预览大屏」入口 —— **一个回合只给一次**，挂在最终回答末尾（任务完成后）。
 *
 * 历史缺陷（2026-11-17 反馈）：入口挂在每条 MCP 工具结果下面，模型逐图调
 * `update_data_screen_chart` 时同一条回答里冒出好几个「预览大屏」，看起来像
 * 每个中间步骤各交付了一个屏。预览是**整块看板**的产物，属于任务终点，不属于某次工具调用。
 *
 * 目标看板从该回合的工具调用里反查（取**最后一个**成功结果）：
 * `create_data_screen` / `update_data_screen_chart` 的返回体带
 * `actions:[{type:'open_screen',dashboard_id,url}]`，历史结果可能只有 dashboard_id/url。
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SquareArrowOutUpRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { ToolCall } from '@/stores/chatStore';
import DashboardPreviewDialog from './asbot/DashboardPreviewDialog';

export interface ScreenPreviewTarget {
  dashboardId: number;
  /** 看板名（返回体里有就用上，让用户知道预览的是哪一块） */
  name?: string;
}

/** 建屏/改图工具名（mcp__datahub_screen__xxx）。 */
const SCREEN_TOOL_RE = /datahub_screen__(create_data_screen|update_data_screen_chart)$/;

function parseScreenTarget(tc: ToolCall): ScreenPreviewTarget | null {
  if (!SCREEN_TOOL_RE.test(tc.tool || '') || tc.error) return null;
  // 落库时 result 只留前 4000 字（首部就是 dashboard_id），后端权威投影在
  // conversation_store.project_message；result_preview 只有 200 字，作末位兜底。
  const raw = tc.result || tc.result_preview || '';
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    const act = (Array.isArray(parsed?.actions) ? parsed.actions : [])
      .find((a: any) => a?.type === 'open_screen');
    const id = Number(act?.dashboard_id || parsed?.dashboard_id || 0);
    if (id) {
      return { dashboardId: id, name: typeof parsed?.name === 'string' ? parsed.name : undefined };
    }
  } catch {
    // 展示层会截断长结果（JSON 不完整），不能因此丢掉入口：按开头出现的看板 id 兜底解析
  }
  const m = raw.match(/"dashboard_id"\s*:\s*(\d+)/) ?? raw.match(/\/screen\/(\d+)/);
  return m ? { dashboardId: Number(m[1]) } : null;
}

/**
 * 该回合的预览目标（去重后按**最后一次出现**排列）。
 *
 * 逐图 update 都带同一个 dashboard_id → 只会得到一个入口（历史缺陷：每条工具结果
 * 各出一个，一条回答里冒出五六个「预览大屏」）。若模型真的建了多块不同的板，
 * 每块各一个入口，但全部堆在回答末尾——不把中间步骤当交付物。
 *
 * 看板名单独回查：`update_data_screen_chart` 的返回体不带 name，
 * 但建屏那一次带了——名字取同 id 的最近一次，别让按钮只剩一串数字 id。
 */
export function screenPreviewTargets(toolCalls?: ToolCall[] | null): ScreenPreviewTarget[] {
  if (!Array.isArray(toolCalls) || !toolCalls.length) return [];
  const ordered: ScreenPreviewTarget[] = [];            // 最后一次出现在前
  const byId = new Map<number, ScreenPreviewTarget>();  // 同时收集 name
  for (let i = toolCalls.length - 1; i >= 0; i -= 1) {
    const hit = parseScreenTarget(toolCalls[i]);
    if (!hit) continue;
    const known = byId.get(hit.dashboardId);
    if (known) {
      if (!known.name && hit.name) known.name = hit.name;
      continue;
    }
    const entry: ScreenPreviewTarget = { dashboardId: hit.dashboardId };
    if (hit.name) entry.name = hit.name;
    byId.set(hit.dashboardId, entry);
    ordered.push(entry);
  }
  return ordered;
}

/** 单目标版（多数回合只有一块板）：取最后一次成功结果所属的看板。 */
export function screenPreviewTarget(toolCalls?: ToolCall[] | null): ScreenPreviewTarget | null {
  return screenPreviewTargets(toolCalls)[0] ?? null;
}

/**
 * 回合级预览入口。`streaming` 时不渲染：任务没结束就给入口等于把中间态当成品。
 */
export function TurnScreenPreview({
  toolCalls, streaming = false,
}: {
  toolCalls?: ToolCall[] | null;
  streaming?: boolean;
}) {
  const { t } = useTranslation('chat');
  const [openId, setOpenId] = useState<number | null>(null);
  const targets = streaming ? [] : screenPreviewTargets(toolCalls);
  if (!targets.length) return null;

  return (
    <>
      <div className="mt-3 flex flex-wrap gap-2">
        {targets.map(target => (
          <Button
            key={target.dashboardId}
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={(e) => { e.stopPropagation(); setOpenId(target.dashboardId); }}
          >
            <SquareArrowOutUpRight className="h-3.5 w-3.5" />
            {t('toolTimeline.previewScreen')}
            {target.name ? (
              <span className="max-w-[240px] truncate font-normal text-muted-foreground">
                · {target.name}
              </span>
            ) : null}
          </Button>
        ))}
      </div>
      {openId ? (
        <DashboardPreviewDialog dashboardId={openId} onClose={() => setOpenId(null)} />
      ) : null}
    </>
  );
}

export default TurnScreenPreview;