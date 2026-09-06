// TraceView：运行轨迹视图（r4，渗透实战执行过程时间线）。
// 时间线 = 纵向节点流：工具步骤（状态图标 + 工具名 + 单行摘要 + 时长，可展开
// 参数/输出）、消息步骤（角色标签 + 单行摘要，可展开全文）、系统标记（小字行）。
// 运行中的工具步骤显示脉冲点。与 ChatView（对话视图）共用同一事件源，
// 由 ConversationView 顶部的视图切换按钮二选一渲染。

import { useMemo, useState } from 'react'
import type { SessionEvent } from '../../connection/api.ts'
import { deriveTrace } from '../../runtime/eventTurns.ts'
import type { TraceStep } from '../../runtime/eventTurns.ts'
import css from './TraceView.module.css'

export interface TraceViewProps {
  events: readonly SessionEvent[]
  /** 空态文案（无事件时）。 */
  emptyText?: string
}

/** 时长格式化：830ms / 12.4s / 3m02s。 */
function formatDuration(ms: number | undefined): string {
  if (ms === undefined) return ''
  if (ms < 1000) return `${ms}ms`
  const total = Math.floor(ms / 1000)
  if (total < 60) return `${total}s`
  return `${Math.floor(total / 60)}m${String(total % 60).padStart(2, '0')}s`
}

/** 时间戳 HH:MM:SS。 */
function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** 状态图标：done ✓ / failed ✕ / running 脉冲点。 */
function StateIcon({ state }: { state: TraceStep['state'] }) {
  if (state === 'running') {
    return <span className={css.pulse} aria-label="执行中" />
  }
  return (
    <span className={css.stateIcon} data-state={state} aria-hidden="true">
      {state === 'done' ? (
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor"
          strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M1.8 5.4L4 7.6L8.2 2.6" />
        </svg>
      ) : (
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor"
          strokeWidth="1.6" strokeLinecap="round">
          <path d="M2.4 2.4L7.6 7.6M7.6 2.4L2.4 7.6" />
        </svg>
      )}
    </span>
  )
}

/** 单个轨迹步骤（点击头部展开/收起详情与输出）。 */
function TraceRow({ step }: { step: TraceStep }) {
  const expandable = step.detail !== undefined || step.output !== undefined
  const [open, setOpen] = useState(false)
  return (
    <div className={css.step} data-kind={step.kind} data-state={step.state}>
      <div className={css.rail}>
        <StateIcon state={step.state} />
        <span className={css.connector} aria-hidden="true" />
      </div>
      <div className={css.body}>
        <div
          className={css.head}
          role={expandable ? 'button' : undefined}
          tabIndex={expandable ? 0 : undefined}
          aria-expanded={expandable ? open : undefined}
          onClick={() => {
            if (expandable) setOpen((value) => !value)
          }}
          onKeyDown={(e) => {
            if (!expandable) return
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              setOpen((value) => !value)
            }
          }}
        >
          {step.kind === 'tool' && <span className={css.tool}>{step.tool}</span>}
          {step.kind === 'message' && (
            <span className={css.role} data-role={step.role}>
              {step.role === 'user' ? '指令' : step.role === 'system' ? '系统' : 'agent'}
            </span>
          )}
          <span className={css.summary}>{step.summary}</span>
          {step.durationMs !== undefined && (
            <span className={css.duration}>{formatDuration(step.durationMs)}</span>
          )}
          <span className={css.time}>{formatTime(step.at)}</span>
        </div>
        {open && step.detail !== undefined && (
          <pre className={css.pre}>{step.detail}</pre>
        )}
        {open && step.output !== undefined && (
          <pre className={css.pre} data-output="">{step.output}</pre>
        )}
      </div>
    </div>
  )
}

export function TraceView({ events, emptyText = '尚无执行轨迹——下发任务后工具调用与结论会出现在这里。' }: TraceViewProps) {
  const steps = useMemo(() => deriveTrace(events), [events])
  const doneCount = steps.filter((step) => step.kind === 'tool' && step.state === 'done').length
  const failedCount = steps.filter((step) => step.kind === 'tool' && step.state === 'failed').length
  return (
    <div className={css.root}>
      <div className={css.toolbar}>
        <span className={css.metric}>{steps.length} 步</span>
        <span className={css.metric} data-tone="ok">工具成功 {doneCount}</span>
        {failedCount > 0 && (
          <span className={css.metric} data-tone="error">失败 {failedCount}</span>
        )}
      </div>
      <div className={css.scroll}>
        <div className={css.flow}>
          {steps.length === 0 && <div className={css.empty}>{emptyText}</div>}
          {steps.map((step) => (
            <TraceRow key={step.key} step={step} />
          ))}
        </div>
      </div>
    </div>
  )
}
