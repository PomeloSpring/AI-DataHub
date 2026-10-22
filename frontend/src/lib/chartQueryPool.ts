/**
 * 看板图表查询的独立资源池 —— **渐进式刷新**的执行底座。
 *
 * 症状（历史缺陷）：`refreshCharts` 用 `Promise.all` 把全部图表一次性打出去，
 * 30 个图表 = 30 个并发请求，而浏览器 HTTP/1.1 每源只给 ~6 条连接，
 * 剩下的排队并且**卡住整个应用的其他请求**（看板转圈 + 其他功能 pending）。
 * 再加上无超时、退出页面不取消，连接被长期占用。
 *
 * 现在的行为：30 个图表也只同时跑 CHART_QUERY_CONCURRENCY 个，其余排队，
 * 前一条完成一条就自动放行下一条（渐进式），并且：
 *   - 每条查询有超时，超时后主动放弃并释放槽位；
 *   - 退出页面时「未执行的直接丢弃、执行中的直接中止」；
 *   - 槽位不会因为超时/取消/抛错而泄漏。
 *
 * 设计取舍：`run()` **永不 reject**，一律 resolve 成 `ChartQueryResult`。
 * 取消是预期行为、超时是可恢复的业务结果，都不该靠 rejection 表达 ——
 * 用 rejection 会在调用方挂上 handler 之前产生 unhandled rejection。
 */

/** 并发上限：留出连接余量给主流程（HTTP/1.1 每源约 6 条） */
export const CHART_QUERY_CONCURRENCY = 4;
/** 单条图表查询的前端超时；后端 socket read_timeout 是 60s，前端先给用户一个可读结论 */
export const CHART_QUERY_TIMEOUT_MS = 20_000;

export type ChartQueryResult<T> =
  | { ok: true; value: T }
  | { ok: false; reason: 'timeout' | 'cancelled' | 'error'; seconds?: number; error?: unknown };

interface Job {
  task: (signal: AbortSignal) => Promise<unknown>;
  settle: (result: ChartQueryResult<unknown>) => void;
  controller?: AbortController;
  timer?: number;
  done?: boolean;
}

class ChartQueryPool {
  private readonly queue: Job[] = [];
  private readonly inflight = new Set<Job>();
  private cancelled = 0;

  constructor(
    private readonly maxConcurrency = CHART_QUERY_CONCURRENCY,
    private readonly timeoutMs = CHART_QUERY_TIMEOUT_MS,
  ) {}

  /** 当前在跑/在排队的总数，供 UI 展示进度与测试断言 */
  get pending() { return this.inflight.size + this.queue.length; }

  /** 排队执行：超过并发上限的先进队列，前一条完成后自动取下一条（渐进式） */
  run<T>(task: (signal: AbortSignal) => Promise<T>, timeoutMs = this.timeoutMs): Promise<ChartQueryResult<T>> {
    return new Promise<ChartQueryResult<T>>(resolve => {
      const job: Job = {
        task,
        settle: result => resolve(result as ChartQueryResult<T>),
      };
      this.queue.push(job);
      this.drain(timeoutMs);
    });
  }

  /** 退出页面 / 切换看板：未执行的直接丢弃，执行中的直接中止 */
  cancelAll() {
    this.cancelled++;
    for (const job of this.queue.splice(0, this.queue.length)) this.finish(job, { ok: false, reason: 'cancelled' });
    for (const job of [...this.inflight]) {
      // 先出队再 abort：任务若不理会 abort 信号也不会把槽位永久占死
      this.finish(job, { ok: false, reason: 'cancelled' });
      job.controller?.abort();
    }
  }

  /** 幂等结算 + 释放槽位：不依赖任务本身 settle，杜绝槽位泄漏 */
  private finish(job: Job, result: ChartQueryResult<unknown>) {
    if (job.done) return;
    job.done = true;
    if (job.timer !== undefined) window.clearTimeout(job.timer);
    this.inflight.delete(job);
    job.settle(result);
    this.drain(this.timeoutMs);
  }

  private drain(timeoutMs: number) {
    while (this.inflight.size < this.maxConcurrency && this.queue.length > 0) {
      this.start(this.queue.shift()!, timeoutMs);
    }
  }

  private start(job: Job, timeoutMs: number) {
    const controller = new AbortController();
    job.controller = controller;
    this.inflight.add(job);
    job.timer = window.setTimeout(() => {
      // 超时必须自己结算：只 abort 不结算的话，任务若无视 signal 就会永远挂住
      this.finish(job, { ok: false, reason: 'timeout', seconds: Math.max(1, Math.round(timeoutMs / 1000)) });
      controller.abort();
    }, timeoutMs);
    job.task(controller.signal).then(
      value => this.finish(job, { ok: true, value }),
      // 超时与主动取消都要归一成可识别的结果，调用方据此决定是否提示用户
      error => this.finish(job, controller.signal.aborted
        ? { ok: false, reason: 'cancelled' }
        : { ok: false, reason: 'error', error }),
    );
  }
}

/** 看板图表查询专用池：与主流程的普通请求分开排队 */
export const chartQueryPool = new ChartQueryPool();
