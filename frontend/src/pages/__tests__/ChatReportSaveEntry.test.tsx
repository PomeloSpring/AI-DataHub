/**
 * 交付文档的「存为报告草稿」接线（源码级护栏）。
 *
 * 为什么读源码而不是渲染整页：Chat.tsx 是三千行的会话页，把它跑起来要桩掉半个应用，
 * 而这里要锁的只有一条接线事实 —— 入口出现了两处，**流程**必须只有一份：
 * 1. 消息操作行与交付文档卡都取 `reportSaveAction(idx, msg)`（同一函数、同一 reportable 判定）；
 * 2. `MarkdownWithCharts` 只把该动作转交给 `ReportCard`，不在中间层另拼一套保存逻辑；
 * 3. 未判 reportable 就不传（卡片里不能出现一个调不通的按钮）。
 * 先例：DialogNestedLayer / ChatStopButton 同类静态护栏。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const chatSrc = readFileSync(resolve(__dirname, '../Chat.tsx'), 'utf8');
const mwSrc = readFileSync(resolve(__dirname, '../../components/MarkdownWithCharts.tsx'), 'utf8');

describe('交付文档的存草稿入口共用一份流程', () => {
  it('会话页只有一份存草稿动作工厂，两处入口都调它', () => {
    expect(chatSrc).toContain('const reportSaveAction = (idx: number, msg: any)');
    // 消息操作行不再是内联字面量（内联第二套 = 其中一套会漏掉 reportable 判定）
    expect(chatSrc).toContain('report={reportSaveAction(idx, msg)}');
    const cardEntries = (chatSrc.match(/onSaveReport=\{reportSaveAction\(idx, msg\)\}/g) || []).length;
    expect(cardEntries).toBeGreaterThanOrEqual(2);   // 中断回合 + Agent 终稿两条渲染路径
  });

  it('reportable 判定仍在工厂里（未判可报告的回合不给入口）', () => {
    const start = chatSrc.indexOf('const reportSaveAction');
    const body = chatSrc.slice(start, start + 700);
    expect(body).toContain('msg?.reportable === true');
    expect(body).toContain('createReportDraftFromTurn(idx, msg)');   // 沿用既有两段式流程
  });

  it('中转层只转交，不自己拼保存逻辑', () => {
    expect(mwSrc).toContain('onSaveReport?: { label?: string; busy?: boolean; onSave: () => void }');
    expect(mwSrc).toContain('saveReport={onSaveReport}');
    // 会话侧的报告接口调用只能留在 Chat.tsx（中间层直接 fetch 会绕过服务端 reportable 判定）
    expect(mwSrc).not.toContain('/chat/report-draft');
    expect(mwSrc).not.toContain('/reports');
  });
});
