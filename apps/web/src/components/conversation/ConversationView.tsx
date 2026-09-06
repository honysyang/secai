// ConversationView：会话中栏组合视图（复刻 dsh ConversationRoot 语义）。
// 结构 = scrollBody（ChatView）+ composerSeat（sticky 底部：审批卡优先，否则 InputBar）。
// 无选中会话时输入栏直接下达任务（自然语言 → run）；有会话时发送指令（steer）。
// 移除右栏详情依赖——资产/风险/报告迁至独立导航视图。

import { useState } from 'react'
import type { SessionSnapshot } from '../../runtime/session.ts'
import type { RespondDecision } from './ApprovalCard.tsx'
import { ApprovalCard } from './ApprovalCard.tsx'
import { ChatView } from './ChatView.tsx'
import { InputBar } from './InputBar.tsx'
import css from './ConversationView.module.css'

export interface ConversationViewProps {
  session: SessionSnapshot | null
  /** 无会话时=直接下达任务（submitOrSend）；有会话时=发送指令（send）。 */
  onSubmit: (text: string) => void | Promise<void>
  onRespond: (rpcId: string, decision: RespondDecision, comment?: string) => void
  /** 打开「新建任务」弹窗（输入栏左侧 + 按钮）。 */
  onNewTask: () => void
}

/** 输入禁用策略：审批挂起 / 会话已进入终态。无会话时可输入（直接下达任务）。 */
function inputGate(session: SessionSnapshot | null): { disabled: boolean; reason: string } {
  if (session === null) return { disabled: false, reason: '' }
  if (session.removed) return { disabled: true, reason: '该目标会话已结束' }
  switch (session.status) {
    case 'awaiting_approval':
      return { disabled: true, reason: '等待人工审批放行（先处理下方审批卡）' }
    case 'completed':
      return { disabled: true, reason: '该目标已完成——报告见「报告」导航' }
    case 'failed':
      return { disabled: true, reason: '该目标已失败终止' }
    case 'stopped':
      return { disabled: true, reason: '该目标已停止' }
    default:
      return { disabled: false, reason: '' }
  }
}

export function ConversationView({ session, onSubmit, onRespond, onNewTask }: ConversationViewProps) {
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
              ? '直接在下方输入目标或任务描述，回车即开始渗透测试。'
              : '会话尚未开始——agent 的消息与工具调用会出现在这里。'
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
          <InputBar
            disabled={gate.disabled}
            disabledReason={gate.reason}
            placeholder={session === null
              ? '输入目标（IP / 域名 / CIDR）或渗透测试任务描述，回车开始'
              : '向该目标下达指令（Enter 发送，Shift+Enter 换行）'}
            onSend={onSubmit}
            onNewTask={onNewTask}
          />
        )}
      </div>
    </div>
  )
}
