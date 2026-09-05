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
          turns.push({ key: event.eventId, kind: 'system', text: label, at: event.createdAt })
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
