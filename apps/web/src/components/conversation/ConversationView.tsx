// ConversationView：会话中栏的组合视图（复刻 dsh ConversationRoot 语义）。
// 根上定义共享宽轴变量：消息列宽轴（748px）与输入卡上限（列宽 + 32px），
// 供 ChatView / InputBar / ApprovalCard 引用同一居中轴。
// 结构 = scrollBody（ChatView，滚动带底部渐隐遮罩）+ composerSeat（sticky 底部
// 座位：有 pendingApprovals 时渲染 ApprovalCard takeover 替代 InputBar）。
// 无顶栏——连接徽章与主题切换已迁至 sidebar footer（任务书）；假设队列/死路
// 折叠不搬，改由 DetailsView 右侧分节呈现。

import { useState } from 'react'
import type { SessionSnapshot } from '../../runtime/session.ts'
import type { RespondDecision } from './ApprovalCard.tsx'
import { ApprovalCard } from './ApprovalCard.tsx'
import { ChatView } from './ChatView.tsx'
import { InputBar } from './InputBar.tsx'
import css from './ConversationView.module.css'

export interface ConversationViewProps {
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
      return { disabled: true, reason: '等待人工审批放行（先处理下方审批卡）' }
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

export function ConversationView({ session, onSend, onRespond }: ConversationViewProps) {
  const [approvalsOpen, setApprovalsOpen] = useState(false)
  const gate = inputGate(session)
  const approvals = session?.pendingApprovals ?? []
  const hasApprovals = approvals.length > 0

  return (
    <div className={css.root}>
      <div className={css.scrollBody}>
        <ChatView
          events={session?.events ?? []}
          running={session?.status === 'running'}
          emptyText={
            session === null
              ? '从左侧选择一个目标会话。'
              : '会话尚未开始——提交任务书后 agent 的消息与工具调用会出现在这里。'
          }
        />
      </div>

      <div className={css.composerSeat}>
        {hasApprovals ? (
          <>
            {approvals.length > 1 && !approvalsOpen && (
              <button
                type="button"
                className={css.moreApprovals}
                onClick={() => setApprovalsOpen(true)}
              >
                还有 {approvals.length - 1} 项待审批——展开全部
              </button>
            )}
            {(approvalsOpen ? approvals : approvals.slice(0, 1)).map((approval) => (
              <ApprovalCard
                key={approval.rpcId}
                rpcId={approval.rpcId}
                payload={approval.payload}
                onRespond={(rpcId, decision, comment) => onRespond(rpcId, decision, comment)}
              />
            ))}
          </>
        ) : (
          <InputBar disabled={gate.disabled} disabledReason={gate.reason} onSend={onSend} />
        )}
      </div>
    </div>
  )
}
