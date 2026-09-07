/** SessionStatusIndicator：会话级状态指示器（顶部栏目标行下方）。 */

import type { SessionStatus } from '../../connection/api.ts'
import { StateDot } from '../primitives/StateDot.tsx'
import css from './SessionStatusIndicator.module.css'

export interface SessionStatusIndicatorProps {
  status: SessionStatus
  runningMs: number
}

/** 状态文案。 */
function statusText(status: SessionStatus): string {
  switch (status) {
    case 'running':
      return '正在执行'
    case 'awaiting_approval':
      return '等待人工审批'
    case 'completed':
      return '已完成'
    case 'failed':
      return '已失败'
    case 'stopped':
      return '已停止'
    default:
      return '待命'
  }
}

/** 状态对应 StateDot 状态。 */
function dotState(status: SessionStatus): 'ongoing' | 'warning' | 'done' | 'error' {
  switch (status) {
    case 'running':
      return 'ongoing'
    case 'awaiting_approval':
      return 'warning'
    case 'completed':
      return 'done'
    case 'failed':
      return 'error'
    default:
      return 'done'
  }
}

function formatElapsed(ms: number): string {
  const total = Math.floor(ms / 1000)
  const s = total % 60
  const m = Math.floor(total / 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export function SessionStatusIndicator({ status, runningMs }: SessionStatusIndicatorProps) {
  return (
    <div className={css.root} role="status" aria-live="polite">
      <StateDot state={dotState(status)} size={8} />
      <span className={css.text}>{statusText(status)}</span>
      {status === 'running' && runningMs > 0 && (
        <span className={css.elapsed}>{formatElapsed(runningMs)}</span>
      )}
    </div>
  )
}
