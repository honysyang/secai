/**
 * ToolOutputPanel：近期工具输出（F3 details）。数据 = 会话事件
 * （tool/call + tool/output 归并见 runtime/eventTurns），倒序列出最近
 * limit 张卡；最新一张默认展开，其余折叠。结果可折叠（<details>）。
 */

import type { SessionEvent } from '../../connection/api.ts'
import { deriveTurns } from '../../runtime/eventTurns.ts'
import type { TurnTool } from '../../runtime/eventTurns.ts'
import { formatClock } from '../../runtime/format.ts'
import css from './ToolOutputPanel.module.css'

export interface ToolOutputPanelProps {
  events: readonly SessionEvent[]
  limit?: number
}

export function ToolOutputPanel({ events, limit = 8 }: ToolOutputPanelProps) {
  const tools = deriveTurns(events)
    .filter((turn): turn is TurnTool => turn.kind === 'tool')
    .slice(-limit)
  if (tools.length === 0) {
    return <div className={css.empty}>尚无工具输出——agent 的工具调用会以可折叠卡片呈现在此。</div>
  }
  return (
    <div className={css.root}>
      {tools.map((tool, index) => (
        <details key={tool.key} className={css.item} open={index === tools.length - 1 || undefined}>
          <summary className={css.head}>
            <span className={css.dot} data-state={tool.state} aria-hidden="true" />
            <span className={css.name}>{tool.name}</span>
            {tool.state === 'failed' && <span className={css.fail}>失败</span>}
            <span className={css.time}>{formatClock(tool.at)}</span>
          </summary>
          <pre className={css.body}>{tool.output ?? '（无输出）'}</pre>
        </details>
      ))}
    </div>
  )
}
