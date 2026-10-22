import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import {
  CHART_QUERY_CONCURRENCY, CHART_QUERY_TIMEOUT_MS,
  chartQueryPool,
} from '../chartQueryPool';

/** 一个可手动控制完成时机的假任务 */
function deferred() {
  let resolve!: (v: unknown) => void, reject!: (e: unknown) => void;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const tick = (ms = 0) => new Promise(res => setTimeout(res, ms));

beforeEach(() => { chartQueryPool.cancelAll(); });
afterEach(() => { chartQueryPool.cancelAll(); });

describe('图表查询资源池（渐进式刷新）', () => {
  it('30 个图表不会一次打满连接：始终只跑 4 个，其余排队渐进放行', async () => {
    let running = 0;
    let peak = 0;
    const gates = Array.from({ length: 30 }, deferred);
    const promises = gates.map((_, i) => chartQueryPool.run(() => {
      running++; peak = Math.max(peak, running);
      return (gates[i].promise as Promise<unknown>).finally(() => { running--; });
    }));

    await tick();
    // 历史缺陷：Promise.all 会同时打 30 个请求，把 HTTP/1.1 每源 ~6 条连接占满
    expect(peak).toBe(CHART_QUERY_CONCURRENCY);
    expect(running).toBe(CHART_QUERY_CONCURRENCY);
    expect(chartQueryPool.pending).toBe(30);

    // 渐进：逐个放行，每完成一个才起下一个，峰值始终不越上限
    const observed: number[] = [];
    for (let i = 0; i < gates.length; i++) {
      gates[i].resolve('ok');
      await tick();
      observed.push(running);
    }
    expect(Math.max(...observed)).toBeLessThanOrEqual(CHART_QUERY_CONCURRENCY);
    const results = await Promise.all(promises);
    expect(results.every(r => r.ok)).toBe(true);
    expect(results.filter(r => r.ok).map(r => (r as { value: string }).value)).toEqual(Array(30).fill('ok'));
    expect(peak).toBe(CHART_QUERY_CONCURRENCY);
    expect(running).toBe(0);
    expect(chartQueryPool.pending).toBe(0);
  });

  it('超时返回 timeout 结果并释放槽位（不 reject，避免 unhandled）', async () => {
    const never = deferred();
    const started: AbortSignal[] = [];
    const pending = chartQueryPool.run(signal => { started.push(signal); return never.promise; }, 20);
    await tick();
    expect(started).toHaveLength(1);
    expect(started[0].aborted).toBe(false);

    await expect(pending).resolves.toMatchObject({ ok: false, reason: 'timeout', seconds: 1 });
    expect(started[0].aborted).toBe(true);

    // 槽位已释放：后面的任务照常执行
    await expect(chartQueryPool.run(() => Promise.resolve('next'))).resolves.toMatchObject({ ok: true, value: 'next' });
  });

  it('取消时未执行的直接丢弃、执行中的直接中止，都返回 cancelled', async () => {
    const gate = deferred();
    const inflight = chartQueryPool.run(() => gate.promise);
    await tick();
    expect(chartQueryPool.pending).toBe(1);

    const queued = [0, 1, 2, 3, 4, 5].map(() => chartQueryPool.run(() => Promise.resolve('never')));
    chartQueryPool.cancelAll();

    await expect(inflight).resolves.toMatchObject({ ok: false, reason: 'cancelled' });
    for (const p of queued) await expect(p).resolves.toEqual({ ok: false, reason: 'cancelled' });
    expect(chartQueryPool.pending).toBe(0);
  });

  it('取消后新提交的查询照常执行（取消不永久关闭池子）', async () => {
    chartQueryPool.cancelAll();
    await expect(chartQueryPool.run(() => Promise.resolve('ok'))).resolves.toMatchObject({ ok: true, value: 'ok' });
  });

  it('任务抛错时返回 error 结果并释放槽位，不卡住后续队列', async () => {
    const failing = chartQueryPool.run(() => Promise.reject(new Error('boom')));
    const next = chartQueryPool.run(() => Promise.resolve('after'));
    await expect(failing).resolves.toMatchObject({ ok: false, reason: 'error' });
    await expect(next).resolves.toMatchObject({ ok: true, value: 'after' });
    expect(chartQueryPool.pending).toBe(0);
  });

  it('默认并发 4、默认超时 20s（前端先给结论，后端 socket read_timeout 是 60s）', () => {
    expect(CHART_QUERY_CONCURRENCY).toBe(4);
    expect(CHART_QUERY_TIMEOUT_MS).toBe(20_000);
    expect(CHART_QUERY_TIMEOUT_MS).toBeLessThan(60_000);
  });
});
