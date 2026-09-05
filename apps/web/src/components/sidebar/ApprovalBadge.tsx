/**
 * ApprovalBadge：目标行的「待审」小徽章（amber）。数据 = approval/
 * requested 帧计数（snapshot.pendingApprovals），点击语义由宿主行负责。
 */

import css from './ApprovalBadge.module.css'

export interface ApprovalBadgeProps {
  count: number
  label?: string
}

export function ApprovalBadge({ count, label = '待审' }: ApprovalBadgeProps) {
  return (
    <span className={css.badge} title={`${count} 项操作等待审批`}>
      {label}
      {count > 1 ? ` ${count}` : ''}
    </span>
  )
}
