/**
 * TargetItem：目标会话行（F3 sidebar）。数据 = host/session-* 帧驱动的
 * SessionSnapshot：
 * - 名称取 header.target，状态圆点 running=蓝(呼吸) / awaiting_approval=
 *   amber / completed=绿 / failed=红 / idle·stopped=灰（dsh 圆点语言）。
 * - 待审批数 > 0 时行末挂 ApprovalBadge（approval/requested 帧计数）。
 * - 子 agent 活动（subagent 事件，runtime/eventTurns.deriveSubagents）
 *   在行下缩进渲染为小状态行。
 */

import type { SessionSnapshot } from '../../runtime/session.ts'
import type { SubagentView } from '../../runtime/eventTurns.ts'
import { formatClock } from '../../runtime/format.ts'
import { ApprovalBadge } from './ApprovalBadge.tsx'
import css from './TargetItem.module.css'

/** 状态 → 中文行内小字（与圆点色同源）。 */
export const STATUS_LABEL: Record<string, string> = {
  idle: '就绪',
  running: '运行中',
  awaiting_approval: '等待审批',
  completed: '已完成',
  failed: '已失败',
  stopped: '已停止',
}

export interface TargetItemProps {
  session: SessionSnapshot
  active: boolean
  subagents?: readonly SubagentView[]
  onSelect: () => void
}

export function TargetItem({ session, active, subagents = [], onSelect }: TargetItemProps) {
  const status = session.removed ? 'stopped' : session.status
  const pending = session.pendingApprovals.length
  return (
    <div>
      <button
        type="button"
        className={css.item}
        data-active={active || undefined}
        onClick={onSelect}
        title={session.header?.target ?? session.sessionId}
      >
        <span className={css.dot} data-status={status} aria-hidden="true" />
        <span className={css.body}>
          <span className={css.name}>{session.header?.target ?? session.sessionId}</span>
          <span className={css.meta}>
            {STATUS_LABEL[status] ?? status}
            {session.lastEventAt !== null ? ` · ${formatClock(session.lastEventAt)}` : ''}
          </span>
        </span>
        {pending > 0 && <ApprovalBadge count={pending} />}
      </button>
      {subagents.map((agent) => (
        <div key={agent.key} className={css.sub} title={agent.role}>
          <span className={css.subDot} aria-hidden="true" />
          <span className={css.subName}>{agent.name}</span>
          <span className={css.subState}>{agent.state}</span>
        </div>
      ))}
    </div>
  )
}
