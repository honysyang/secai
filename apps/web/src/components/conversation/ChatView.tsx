/**
 * ChatView：消息气泡流（F3）。事件 → 轮次归并见 runtime/eventTurns
 * deriveTurns：user/assistant 两种气泡（assistant = bubble 面、user =
 * business 蓝反白）+ tool 卡（<details> 结果可折叠，运行中自动展开）+
 * 系统小字行。自身是滚动容器（会话情报条与输入条在其外固定）。
 */

import { useEffect, useRef } from 'react'
import type { SessionEvent } from '../../connection/api.ts'
import { deriveTurns } from '../../runtime/eventTurns.ts'
import type { TurnAssistant, TurnSystem, TurnTool, TurnUser } from '../../runtime/eventTurns.ts'
import { formatClock } from '../../runtime/format.ts'
import type { TimelineTurn } from '../../runtime/eventTurns.ts'
import css from './ChatView.module.css'

export interface ChatViewProps {
  events: readonly SessionEvent[]
  /** 空态文案（无事件时）。 */
  emptyText?: string
}

function UserBubble({ turn }: { turn: TurnUser }) {
  return (
    <div className={css.rowRight}>
      <div className={`${css.bubble} ${css.user}`}>{turn.text}</div>
    </div>
  )
}

function AssistantBubble({ turn }: { turn: TurnAssistant }) {
  return (
    <div className={css.rowLeft}>
      <div className={`${css.bubble} ${css.assistant}`}>{turn.text}</div>
    </div>
  )
}

function ToolCard({ turn }: { turn: TurnTool }) {
  return (
    <details className={css.tool} open>
      <summary className={css.toolHead}>
        <span className={css.chev} aria-hidden="true" />
        <span className={css.toolDot} data-state={turn.state} aria-hidden="true" />
        <span className={css.toolName}>{turn.name}</span>
        {turn.state === 'failed' && <span className={css.toolFail}>失败</span>}
        <span className={css.toolTime}>{formatClock(turn.at)}</span>
      </summary>
      <pre className={css.toolBody}>{turn.output ?? '（无输出）'}</pre>
    </details>
  )
}

function SystemLine({ turn }: { turn: TurnSystem }) {
  return <div className={css.sys}>{turn.text}</div>
}

function TurnRow({ turn }: { turn: TimelineTurn }) {
  switch (turn.kind) {
    case 'user':
      return <UserBubble turn={turn} />
    case 'assistant':
      return <AssistantBubble turn={turn} />
    case 'tool':
      return <ToolCard turn={turn} />
    default:
      return <SystemLine turn={turn} />
  }
}

export function ChatView({ events, emptyText = '会话尚未开始——提交任务书后 agent 的消息与工具调用会出现在这里。' }: ChatViewProps) {
  const turns = deriveTurns(events)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  // 新事件到达即贴底（仅计数变化时，避免打断用户上翻）
  useEffect(() => {
    const el = scrollRef.current
    if (el !== null) el.scrollTop = el.scrollHeight
  }, [events.length])

  return (
    <div className={css.scroll} ref={scrollRef} role="log" aria-live="polite">
      <div className={css.inner}>
        {turns.length === 0 && <div className={css.empty}>{emptyText}</div>}
        {turns.map((turn) => <TurnRow key={turn.key} turn={turn} />)}
      </div>
    </div>
  )
}
