// TargetItem：侧边栏目标行（复刻 dsh ui-workspace sessionRow）。
// 32px 高、radius 8、padding 0 8px；16x20 前导槽放 StateDot；
// title 14/20 省略；time 12/20 tertiary，hover 时 time 隐藏、rowActions 显形
// （纯 CSS）。状态映射：running→ongoing(蓝)、awaiting_approval→warning(琥珀)、
// failed→error(红)、completed→done(绿)、idle/stopped→灰点。

import type { SessionSnapshot } from '../../runtime/session.ts'
import { StateDot } from '../primitives/StateDot.tsx'
import type { StateDotState } from '../primitives/StateDot.tsx'
import { formatClock } from '../../runtime/format.ts'
import css from './TargetItem.module.css'

/** SECAI 会话状态 → dsh StateDot 四态（idle/stopped 无对应色，用灰 caption 点）。 */
function dotState(status: SessionSnapshot['status'], removed: boolean): StateDotState | 'idle' {
  if (removed) return 'idle'
  switch (status) {
    case 'running':
      return 'ongoing'
    case 'awaiting_approval':
      return 'warning'
    case 'failed':
      return 'error'
    case 'completed':
      return 'done'
    default:
      return 'idle'
  }
}

export interface TargetItemProps {
  session: SessionSnapshot
  selected: boolean
  onSelect: (sessionId: string) => void
}

export function TargetItem({ session, selected, onSelect }: TargetItemProps) {
  const target = session.header?.target ?? session.sessionId
  const state = dotState(session.removed ? 'stopped' : session.status, session.removed)
  const time = formatClock(session.lastEventAt ?? session.header?.updatedAt)
  return (
    <div
      className={css.sessionRow}
      data-selected={selected || undefined}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(session.sessionId)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(session.sessionId)
        }
      }}
    >
      <span className={css.slot}>
        {state === 'idle' ? (
          <span className={css.idleDot} aria-hidden="true" />
        ) : (
          <StateDot state={state} size={10} />
        )}
      </span>
      <span className={css.title} title={target}>{target}</span>
      <span className={css.time} data-time="">{time}</span>
      {/* 行尾操作占位（dsh 为 ellipsis 菜单钮；SECAI 暂无可挂动作，纯视觉位） */}
      <span className={css.rowActions} data-actions="">
        <EllipsisIcon />
      </span>
    </div>
  )
}

function EllipsisIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
      <circle cx="3" cy="7" r="1.2" />
      <circle cx="7" cy="7" r="1.2" />
      <circle cx="11" cy="7" r="1.2" />
    </svg>
  )
}
