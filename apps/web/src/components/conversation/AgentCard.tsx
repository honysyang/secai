/** AgentCard：子 agent 折叠卡（名称 + 状态 + 展开动作流）。 */

import { useState } from 'react'
import type { SessionEvent } from '../../connection/api.ts'
import { StateDot } from '../primitives/StateDot.tsx'
import { ToolRow } from './ToolRow.tsx'
import css from './AgentCard.module.css'

export interface AgentCardProps {
  agentName: string
  events: readonly SessionEvent[]
  state: 'running' | 'done' | 'error'
}

/** 从事件中提取 agent 动作摘要。 */
function agentSummary(events: readonly SessionEvent[]): string {
  for (const event of events) {
    if (event.type === 'agent/tool_start' || event.type === 'tool/started') {
      const data = event.data as Record<string, unknown>
      const name = data['name'] as string | undefined
      if (name) return name
    }
  }
  return ''
}

export function AgentCard({ agentName, events, state }: AgentCardProps) {
  const [open, setOpen] = useState(false)
  const summary = agentSummary(events)

  return (
    <div className={css.root} data-state={state}>
      <button
        type="button"
        className={css.header}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <StateDot
          state={state === 'running' ? 'ongoing' : state === 'error' ? 'error' : 'done'}
          size={10}
        />
        <span className={css.name}>{agentName}</span>
        {summary !== '' && <span className={css.summary}>{summary}</span>}
        <span className={css.chevron} aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className={css.body}>
          {events.length === 0 ? (
            <div className={css.empty}>暂无动作</div>
          ) : (
            events.map((event) => {
              const data = event.data as Record<string, unknown>
              if (event.type === 'agent/tool_start' || event.type === 'tool/started') {
                const name = (data['name'] as string) ?? 'tool'
                const args = data['args'] !== undefined ? JSON.stringify(data['args']) : ''
                return (
                  <ToolRow
                    key={event.eventId}
                    title={name}
                    summary={args.slice(0, 80)}
                    body={args}
                    output={null}
                    state="ok"
                  />
                )
              }
              if (event.type === 'agent/tool_end' || event.type === 'tool/completed') {
                const name = (data['name'] as string) ?? 'tool'
                const output = data['output'] !== undefined ? String(data['output']) : ''
                return (
                  <div key={event.eventId} className={css.toolEnd}>
                    {name} → {output.slice(0, 60)}
                  </div>
                )
              }
              return (
                <div key={event.eventId} className={css.eventLine}>
                  {event.type}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
