// ChatView：消息流（复刻 dsh ui-conversation ChatView 的简化版）。
// 滚动区 + 居中 column（gap 16px）：user 消息 = 右对齐气泡行
// （.userStack max-width min(525px,82%) + .bubble radius22 padding 10x16 16/24
// bubble 底色）；assistant 全宽纯文本列（无气泡）；tool 轨迹 = ToolRow；
// system = 小字行。运行中显示 "Deep diving..." 扫光字（background-clip:text
// 渐变 1.8s 循环，>15s 追加时钟）。回到底部 sticky 圆形按钮 34px。
// 底贴跟随：新 user 消息强制贴底，其余仅当已贴底时跟随。

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { SessionEvent } from '../../connection/api.ts'
import { deriveTurns } from '../../runtime/eventTurns.ts'
import type { TimelineTurn, TurnTool } from '../../runtime/eventTurns.ts'
import { ToolRow } from './ToolRow.tsx'
import css from './ChatView.module.css'

const FOLLOW_THRESHOLD = 24

export interface ChatViewProps {
  events: readonly SessionEvent[]
  /** 运行中（显示扫光状态行）。 */
  running: boolean
  /** 空态文案（无事件时）。 */
  emptyText?: string
}

/** user 气泡行：右对齐列（figma User_Bubble 659:38813）。 */
function UserRow({ text }: { text: string }) {
  return (
    <div className={css.userRow}>
      <div className={css.userStack}>
        <div className={css.bubble}>{text}</div>
      </div>
    </div>
  )
}

/** assistant 全宽 markdown 文本列（无气泡）。 */
function AssistantRow({ text }: { text: string }) {
  return <div className={css.assistant}>{text}</div>
}

/** 系统小字行（子 agent 活动 / 假设推进等）。 */
function SystemRow({ text }: { text: string }) {
  return <div className={css.system}>{text}</div>
}

/** 工具轨迹行 → ToolRow（IN/OUT 可展开）。 */
function ToolTurnRow({ turn }: { turn: TurnTool }) {
  const summary = toolSummary(turn)
  const failure = turn.state === 'failed' ? firstLine(turn.output) : null
  return (
    <ToolRow
      title={turn.name}
      summary={summary}
      errorSummary={failure}
      body={turn.args ?? null}
      output={turn.output ?? null}
      state={turn.state === 'running' ? 'running' : turn.state === 'failed' ? 'error' : 'ok'}
    />
  )
}

/** 折叠态摘要：参数/输出的一句话（首行截断）。 */
function toolSummary(turn: TurnTool): string {
  const source = turn.args ?? turn.output ?? ''
  return firstLine(source) ?? ''
}

function firstLine(text: string | undefined): string | null {
  if (text === undefined || text === '') return null
  const line = text.split('\n', 1)[0] ?? ''
  return line === '' ? null : line
}

/** 运行时长格式化（mm:ss；>1h 用 h:mm:ss）。 */
function formatElapsed(ms: number): string {
  const total = Math.floor(ms / 1000)
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = (v: number): string => String(v).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

/** 运行状态行：扫光字 + 可选时钟（>15s）。 */
function TurnStatus() {
  const [mountedAt] = useState(() => Date.now())
  const [elapsedMs, setElapsedMs] = useState(0)
  useEffect(() => {
    const tick = (): void => setElapsedMs(Math.max(0, Date.now() - mountedAt))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [mountedAt])
  const showClock = elapsedMs >= 15_000
  return (
    <div className={css.turnStatus} role="status" aria-live="polite">
      Deep diving...
      {showClock && (
        <span className={css.turnStatusClock} aria-hidden="true">
          {formatElapsed(elapsedMs)}
        </span>
      )}
    </div>
  )
}

function TurnRow({ turn }: { turn: TimelineTurn }) {
  switch (turn.kind) {
    case 'user':
      return <UserRow text={turn.text} />
    case 'assistant':
      return <AssistantRow text={turn.text} />
    case 'tool':
      return <ToolTurnRow turn={turn} />
    default:
      return <SystemRow text={turn.text} />
  }
}

export function ChatView({
  events,
  running,
  emptyText = '会话尚未开始——提交任务书后 agent 的消息与工具调用会出现在这里。',
}: ChatViewProps) {
  const turns = useMemo(() => deriveTurns(events), [events])
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const atBottomRef = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  const lastUserKeyRef = useRef<string | null>(null)

  const lastUserKey = useMemo(() => {
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      if (turns[i]!.kind === 'user') return turns[i]!.key
    }
    return null
  }, [turns])

  const toBottom = (): void => {
    const el = scrollRef.current
    if (el === null) return
    el.scrollTop = el.scrollHeight
    atBottomRef.current = true
    setAtBottom(true)
  }

  // 流变化后的跟随决策：自己的新 user 消息必须可见（强制贴底）；
  // 其余内容仅当已贴底时跟随（不打断上翻阅读）。
  useLayoutEffect(() => {
    const appendedUser = lastUserKey !== null && lastUserKey !== lastUserKeyRef.current
    lastUserKeyRef.current = lastUserKey
    if (appendedUser || atBottomRef.current) toBottom()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turns.length, lastUserKey, running])

  const onScroll = (): void => {
    const el = scrollRef.current
    if (el === null) return
    const floor = Math.max(0, el.scrollHeight - el.clientHeight)
    const isAtBottom = floor - el.scrollTop <= FOLLOW_THRESHOLD + 1
    atBottomRef.current = isAtBottom
    setAtBottom(isAtBottom)
  }

  return (
    <div className={css.root}>
      <div ref={scrollRef} className={css.scroll} onScroll={onScroll} role="log" aria-live="polite">
        <div className={css.column} data-chat-flow>
          {turns.length === 0 && <div className={css.empty}>{emptyText}</div>}
          {turns.map((turn) => (
            <div className={css.flowItem} key={turn.key} data-chat-anchor-key={turn.key}>
              <TurnRow turn={turn} />
            </div>
          ))}
          {running && <TurnStatus />}
        </div>
        {!atBottom && (
          <div className={css.toBottomSlot}>
            <button type="button" className={css.toBottom} aria-label="回到底部" onClick={toBottom}>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
                strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 5.5L7 9.5L11 5.5" />
              </svg>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
