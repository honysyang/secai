/**
 * ConversationView：会话中栏的组合视图（F3 编排）。从当前 SessionSnapshot
 * 取数分发给子组件：ApprovalCard（approval/requested 帧 → respond）、
 * ChatView（events → 气泡流）、HypothesisQueue（queue 按 priority 排序）、
 * DeadEndList（projection.dead_ends 折叠）、InputBar（禁用态/审批提示）。
 * 顶栏放连接徽章（DEMO/已连接）与 ThemeToggle。
 */

import type { SessionSnapshot } from '../../runtime/session.ts'
import { parseDeadEnds } from '../../runtime/projections.ts'
import type { ThemeControl } from '../theme/theme.ts'
import { ThemeToggle } from '../theme/ThemeToggle.tsx'
import type { RespondDecision } from './ApprovalCard.tsx'
import { ApprovalCard } from './ApprovalCard.tsx'
import { ChatView } from './ChatView.tsx'
import { DeadEndList } from './DeadEndList.tsx'
import { HypothesisQueue } from './HypothesisQueue.tsx'
import { InputBar } from './InputBar.tsx'
import css from './ConversationView.module.css'

export type LinkTone = 'demo' | 'live' | 'offline'

export interface ConversationViewProps {
  theme: ThemeControl
  /** 连接徽章文案（DEMO / 已连接 / 重连中…）。 */
  linkLabel: string
  linkTone: LinkTone
  session: SessionSnapshot | null
  onSend: (text: string) => void
  onRespond: (rpcId: string, decision: RespondDecision, comment?: string) => void
}

/** 输入禁用策略：无会话 / 审批挂起 / 会话已进入终态。 */
function inputGate(session: SessionSnapshot | null): { disabled: boolean; reason: string } {
  if (session === null) return { disabled: true, reason: '从左侧选择一个目标会话后下达指令' }
  if (session.removed) return { disabled: true, reason: '该目标会话已结束' }
  switch (session.status) {
    case 'awaiting_approval':
      return { disabled: true, reason: '等待人工审批放行（先处理上方审批卡）' }
    case 'completed':
      return { disabled: true, reason: '该目标已完成——报告见右侧详情' }
    case 'failed':
      return { disabled: true, reason: '该目标已失败终止' }
    case 'stopped':
      return { disabled: true, reason: '该目标已停止' }
    default:
      return { disabled: false, reason: '' }
  }
}

export function ConversationView({
  theme, linkLabel, linkTone, session, onSend, onRespond,
}: ConversationViewProps) {
  const gate = inputGate(session)
  const deadEnds = session !== null ? parseDeadEnds(session.projections.dead_ends) : null
  const queue = session?.queue ?? []
  const approvals = session?.pendingApprovals ?? []

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.dot} data-status={session === null ? undefined : (session.removed ? 'stopped' : session.status)} aria-hidden="true" />
        <div className={css.titles}>
          <div className={css.title}>{session?.header?.target ?? 'SECAI-PT · 破阵'}</div>
          <div className={css.sub}>
            {session === null ? '未选择目标' : `${session.sessionId} · ${session.counts.events} 事件`}
          </div>
        </div>
        <div className={css.tools}>
          <span className={css.linkBadge} data-tone={linkTone} title={linkLabel}>{linkLabel}</span>
          <ThemeToggle control={theme} />
        </div>
      </div>

      {approvals.length > 0 && (
        <div className={css.approvals} role="region" aria-label="待审批操作">
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.rpcId}
              rpcId={approval.rpcId}
              payload={approval.payload}
              onRespond={(rpcId, decision, comment) => onRespond(rpcId, decision, comment)}
            />
          ))}
        </div>
      )}

      <ChatView events={session?.events ?? []} />

      {queue.length > 0 && <HypothesisQueue items={queue} />}
      {deadEnds !== null && <DeadEndList items={deadEnds} />}

      <InputBar
        disabled={gate.disabled}
        disabledReason={gate.reason}
        pendingApprovals={session?.pendingApprovals.length ?? 0}
        onSend={onSend}
      />
    </div>
  )
}
