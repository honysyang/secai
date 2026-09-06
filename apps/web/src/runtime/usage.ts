/**
 * usage.ts —— 会话 token 用量聚合（r5：token 计数 + 运行速率显示）。
 *
 * 数据源 = 后端 runner 的 `usage` 事件（_LlmTurnHooks.on_llm_end 发射：
 * { model, inputTokens, outputTokens, totalTokens, durationMs }）。
 * 速率口径：decode 侧无流式，generation 速率 = ΣoutputTokens / ΣdurationMs；
 * 速率只反映 LLM 生成窗口，不含工具执行/审批等待（对话面板另显会话墙钟）。
 */

import type { SessionEvent } from '../connection/api.ts'

/** 会话累计用量快照（deriveUsage 纯投影产物）。 */
export interface UsageStats {
  /** LLM 调用次数。 */
  calls: number
  inputTokens: number
  outputTokens: number
  totalTokens: number
  /** Σ LLM 调用墙钟时长（ms，生成窗口合计）。 */
  genMs: number
  /** 生成速率 tok/s（输出 tokens / 生成窗口；genMs=0 → 0）。 */
  tokPerSec: number
  /** 最近一次调用的模型名（空串 = 未知）。 */
  model: string
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
}

/** 从事件序列聚合累计 token 用量与生成速率（usage 事件缺失 = 全零）。 */
export function deriveUsage(events: readonly SessionEvent[]): UsageStats {
  let calls = 0
  let inputTokens = 0
  let outputTokens = 0
  let totalTokens = 0
  let genMs = 0
  let model = ''
  for (const event of events) {
    if (event.type !== 'usage') continue
    const data = event.data
    calls += 1
    inputTokens += num(data.inputTokens)
    outputTokens += num(data.outputTokens)
    totalTokens += num(data.totalTokens)
    genMs += num(data.durationMs)
    const name = typeof data.model === 'string' ? data.model : ''
    if (name !== '') model = name
  }
  const tokPerSec = genMs > 0 ? outputTokens / (genMs / 1000) : 0
  return { calls, inputTokens, outputTokens, totalTokens, genMs, tokPerSec, model }
}

/** token 数缩写（1234 → 1.2k；12345 → 12.3k；1234567 → 1.2M）。 */
export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
  return String(count)
}

/** 速率缩写（42.3 → "42 tok/s"；一位小数仅在 <10 时保留）。 */
export function formatRate(tokPerSec: number): string {
  if (tokPerSec <= 0) return '0 tok/s'
  const value = tokPerSec >= 10 ? Math.round(tokPerSec) : Math.round(tokPerSec * 10) / 10
  return `${value} tok/s`
}
