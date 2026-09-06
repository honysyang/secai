/**
 * 会话事件 → 对话/时间线渲染模型（F3 ChatView / ToolOutputPanel 共用）。
 *
 * 事件 type 约定（对齐后端 server/ 事件落库口径，demo 帧同一约定）：
 * - message:      data { role: 'user' | 'assistant', content } → 气泡
 * - tool/call:    data { tool, args? } → 打开一张工具卡
 * - tool/output:  data { tool?, output?, ok?, durationMs? } → 归并到最近
 *                 未闭合工具卡；无 open 卡时自成一张已完成卡
 * - subagent:     data { name, role?, state? } → 子 agent 活动系统行
 * - 其余 type（hypothesis / finding / …）→ 系统行
 */

import type { SessionEvent } from '../connection/api.ts'

export interface TurnUser {
  key: string
  kind: 'user'
  text: string
  at: string
}

export interface TurnAssistant {
  key: string
  kind: 'assistant'
  text: string
  at: string
}

export interface TurnTool {
  key: string
  kind: 'tool'
  name: string
  state: 'running' | 'done' | 'failed'
  args?: string
  output?: string
  at: string
}

export interface TurnSystem {
  key: string
  kind: 'system'
  text: string
  /** 展示语气（steer 回执等本地事件用 error 红字提示；缺省中性）。 */
  tone?: 'ok' | 'error'
  at: string
}

export type TimelineTurn = TurnUser | TurnAssistant | TurnTool | TurnSystem

interface OpenTool {
  key: string
  name: string
  args?: string
  output?: string
  state: 'running' | 'done' | 'failed'
  at: string
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function pushOrClose(turns: TimelineTurn[], open: OpenTool | null): OpenTool | null {
  if (open !== null) {
    turns.push({
      key: open.key,
      kind: 'tool',
      name: open.name,
      state: open.state,
      args: open.args,
      output: open.output,
      at: open.at,
    })
    return null
  }
  return null
}

/** 把事件序列归并为对话/时间线轮次（按 seq 升序；窗口截断不影响归并）。 */
export function deriveTurns(events: readonly SessionEvent[]): TimelineTurn[] {
  const turns: TimelineTurn[] = []
  let open: OpenTool | null = null
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    const data = asRecord(event.data) ?? {}
    switch (event.type) {
      case 'message': {
        open = pushOrClose(turns, open)
        const role = str(data.role, 'assistant')
        if (role === 'user') {
          turns.push({ key: event.eventId, kind: 'user', text: str(data.content), at: event.createdAt })
        } else {
          turns.push({ key: event.eventId, kind: 'assistant', text: str(data.content), at: event.createdAt })
        }
        break
      }
      case 'usage': {
        // token 用量事件：进 usage 聚合（usage.ts），不进对话流
        open = pushOrClose(turns, open)
        break
      }
      case 'tool/call': {
        open = pushOrClose(turns, open)
        open = {
          key: event.eventId,
          name: str(data.tool, str(data.name, 'tool')),
          args: data.args !== undefined ? JSON.stringify(data.args) : undefined,
          state: 'running',
          at: event.createdAt,
        }
        break
      }
      case 'tool/output': {
        const name = str(data.tool, str(data.name, ''))
        if (open !== null && (name === '' || name === open.name)) {
          open.output = str(data.output, str(data.result, ''))
          open.state = data.ok === false ? 'failed' : 'done'
        } else {
          turns.push({
            key: event.eventId,
            kind: 'tool',
            name: name === '' ? 'tool' : name,
            state: data.ok === false ? 'failed' : 'done',
            output: str(data.output, str(data.result, '')),
            at: event.createdAt,
          })
        }
        break
      }
      default: {
        open = pushOrClose(turns, open)
        if (event.type === 'subagent') {
          const state = str(data.state, '')
          turns.push({
            key: event.eventId,
            kind: 'system',
            text: `子 agent「${str(data.name, '?')}」${state === '' ? '' : `：${state}`}`,
            at: event.createdAt,
          })
        } else {
          const label = str(data.label, event.type)
          const tone = data.tone === 'error' ? 'error' as const : undefined
          turns.push({ key: event.eventId, kind: 'system', text: label, tone, at: event.createdAt })
        }
      }
    }
  }
  pushOrClose(turns, open)
  return turns
}

/** 活跃子 agent 视图：按名字取最近一次 subagent 事件（去重保序）。 */
export interface SubagentView {
  key: string
  name: string
  role?: string
  state: string
  lastAt: string
}

/** 从事件序列提取子 agent 最新状态（TargetList 缩进子行数据源）。 */
export function deriveSubagents(events: readonly SessionEvent[]): SubagentView[] {
  const order: string[] = []
  const latest = new Map<string, SubagentView>()
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.type !== 'subagent') continue
    const data = asRecord(event.data) ?? {}
    const name = str(data.name, str(data.id, ''))
    if (name === '') continue
    const view: SubagentView = {
      key: `${name}:${event.eventId}`,
      name,
      role: data.role !== undefined ? str(data.role) : undefined,
      state: str(data.state, 'running'),
      lastAt: event.createdAt,
    }
    if (!latest.has(name)) order.push(name)
    latest.set(name, view)
  }
  return order.map((name) => latest.get(name)!)
}

// ─────────────────────────────────────────────────────────────────────────────
// 运行轨迹投影（TraceView 数据源，r4：渗透实战执行过程视图）
// ─────────────────────────────────────────────────────────────────────────────

/** 轨迹步骤：执行时间线上的一个节点（工具/消息/系统标记）。 */
export interface TraceStep {
  key: string
  kind: 'tool' | 'message' | 'system'
  /** 工具名（kind=tool）。 */
  tool?: string
  state: 'running' | 'done' | 'failed'
  /** 单行摘要（args 首行 / 消息首行 / 系统文本）。 */
  summary: string
  /** 可展开详情（工具 args JSON / 消息全文）。 */
  detail?: string
  /** 工具输出（展开查看原始输出）。 */
  output?: string
  /** 消息角色（kind=message）。 */
  role?: 'user' | 'assistant' | 'system'
  /** 工具执行时长（output 到达时间 − call 时间；运行中未定 = undefined）。 */
  durationMs?: number
  at: string
}

function tsMs(iso: string): number {
  const value = Date.parse(iso)
  return Number.isNaN(value) ? 0 : value
}

/** 一行摘要：首个非空行（截断 160 字符，工具摘要优先参数）。 */
function traceSummary(source: string): string {
  const line = source.split('\n', 1)[0] ?? ''
  const text = line.trim()
  return text.length > 160 ? `${text.slice(0, 160)}…` : text
}

/**
 * 事件序列 → 执行轨迹（按 seq 升序）。
 *
 * 工具卡 = call+output 归并（带时长与状态）；消息压成一行摘要（可展开全文）；
 * usage/system 等不进轨迹（usage 进聚合，system 仅保留带 label 的人类可读项）。
 */
export function deriveTrace(events: readonly SessionEvent[]): TraceStep[] {
  const steps: TraceStep[] = []
  interface OpenTool {
    key: string
    name: string
    args?: string
    at: string
  }
  let open: OpenTool | null = null

  const flush = (): void => {
    if (open === null) return
    steps.push({
      key: open.key,
      kind: 'tool',
      tool: open.name,
      state: 'failed',
      summary: traceSummary(open.args ?? ''),
      detail: open.args,
      at: open.at,
    })
    open = null
  }

  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    const data = asRecord(event.data) ?? {}
    switch (event.type) {
      case 'tool/call': {
        flush()
        open = {
          key: event.eventId,
          name: str(data.tool, str(data.name, 'tool')),
          args: data.args !== undefined ? JSON.stringify(data.args) : undefined,
          at: event.createdAt,
        }
        break
      }
      case 'tool/output': {
        const name = str(data.tool, str(data.name, ''))
        const output = str(data.output, str(data.result, ''))
        const failed = data.ok === false
        if (open !== null && (name === '' || name === open.name)) {
          steps.push({
            key: open.key,
            kind: 'tool',
            tool: open.name,
            state: failed ? 'failed' : 'done',
            summary: traceSummary(open.args ?? output),
            detail: open.args,
            output: output === '' ? undefined : output,
            durationMs: Math.max(0, tsMs(event.createdAt) - tsMs(open.at)),
            at: open.at,
          })
          open = null
        } else {
          flush()
          steps.push({
            key: event.eventId,
            kind: 'tool',
            tool: name === '' ? 'tool' : name,
            state: failed ? 'failed' : 'done',
            summary: traceSummary(output),
            output: output === '' ? undefined : output,
            at: event.createdAt,
          })
        }
        break
      }
      case 'message': {
        flush()
        const role = str(data.role, 'assistant') as TraceStep['role']
        const content = str(data.content)
        if (content === '') break
        steps.push({
          key: event.eventId,
          kind: 'message',
          state: 'done',
          role,
          summary: traceSummary(content),
          detail: content,
          at: event.createdAt,
        })
        break
      }
      case 'subagent': {
        flush()
        const state = str(data.state, '')
        steps.push({
          key: event.eventId,
          kind: 'system',
          state: 'done',
          summary: `子 agent「${str(data.name, '?')}」${state === '' ? '' : `：${state}`}`,
          at: event.createdAt,
        })
        break
      }
      default:
        break // usage / 未知类型不进轨迹
    }
  }
  flush()
  return steps
}
