// ConversationView：会话中栏组合视图（复刻 dsh ConversationRoot 语义）。
// 结构 = 顶部会话状态栏（目标 + 状态 + token 计数/生成速率 + 对话/轨迹视图切换）
// + scrollBody（ChatView 对话流 / TraceView 运行轨迹）+ composerSeat（sticky 底部：
// 审批卡优先，否则 InputBar）。无选中会话时输入栏直接下达任务（自然语言 → run）；
// 有会话时发送指令（steer）。

import { useMemo, useState } from 'react'
import type { SessionSnapshot } from '../../runtime/session.ts'
import { deriveUsage, formatRate, formatTokens } from '../../runtime/usage.ts'
import type { RespondDecision } from './ApprovalCard.tsx'
import { ApprovalCard } from './ApprovalCard.tsx'
import { ChatView } from './ChatView.tsx'
import { InputBar } from './InputBar.tsx'
import { LinkView } from './LinkView.tsx'
import { TraceView } from './TraceView.tsx'
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

/** 会话状态中文标签（顶栏状态胶囊）。 */
function statusLabel(status: SessionSnapshot['status']): { text: string; tone: string } {
  switch (status) {
    case 'running':
      return { text: '执行中', tone: 'running' }
    case 'awaiting_approval':
      return { text: '待审批', tone: 'approval' }
    case 'completed':
      return { text: '已完成', tone: 'done' }
    case 'failed':
      return { text: '已失败', tone: 'failed' }
    case 'stopped':
      return { text: '已停止', tone: 'stopped' }
    default:
      return { text: '待命', tone: 'idle' }
  }
}

/** 顶部会话状态栏：目标名 + 状态胶囊 + token 计数/生成速率 + 视图切换。 */
function ConversationHeader({
  session,
  view,
  onView,
}: {
  session: SessionSnapshot
  view: 'chat' | 'trace' | 'link'
  onView: (view: 'chat' | 'trace' | 'link') => void
}) {
  const usage = useMemo(() => deriveUsage(session.events), [session.events])
  const status = statusLabel(session.status)
  const target = session.header?.target ?? session.sessionId
  return (
    <div className={css.header}>
      <span className={css.headerTarget} title={target}>{target}</span>
      <span className={css.headerStatus} data-tone={status.tone}>{status.text}</span>
      <span className={css.headerSpacer} />
      {usage.calls > 0 && (
        <span
          className={css.usage}
          title={`输入 ${usage.inputTokens} tok · 输出 ${usage.outputTokens} tok · ${usage.calls} 次 LLM 调用${usage.model !== '' ? ` · ${usage.model}` : ''}`}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor"
            strokeWidth="1.2" strokeLinecap="round" aria-hidden="true">
            <circle cx="6" cy="6" r="4.6" />
            <path d="M6 3.6V6l1.8 1.2" />
          </svg>
          {formatTokens(usage.totalTokens)} tok
          <span className={css.usageRate}>{formatRate(usage.tokPerSec)}</span>
        </span>
      )}
      <div className={css.viewToggle} role="tablist" aria-label="视图切换">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'chat'}
          className={css.viewTab}
          data-active={view === 'chat' || undefined}
          onClick={() => onView('chat')}
        >
          对话
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'trace'}
          className={css.viewTab}
          data-active={view === 'trace' || undefined}
          onClick={() => onView('trace')}
        >
          轨迹
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'link'}
          className={css.viewTab}
          data-active={view === 'link' || undefined}
          onClick={() => onView('link')}
        >
          渗透
        </button>
      </div>
    </div>
  )
}

export function ConversationView({ session, onSubmit, onRespond, onNewTask }: ConversationViewProps) {
  const [approvalsOpen, setApprovalsOpen] = useState(false)
  const [view, setView] = useState<'chat' | 'trace' | 'link'>('chat')
  const gate = inputGate(session)
  const approvals = session?.pendingApprovals ?? []
  const hasApprovals = approvals.length > 0

  return (
    <div className={css.root}>
      {session !== null && (
        <ConversationHeader session={session} view={view} onView={setView} />
      )}
      <div className={css.scrollBody}>
        {view === 'trace' && session !== null ? (
          <TraceView events={session.events} />
        ) : view === 'link' && session !== null ? (
          <LinkView events={session.events} />
        ) : (
          <ChatView
            events={session?.events ?? []}
            running={session?.status === 'running'}
            emptyText={
              session === null
                ? '直接在下方输入目标或任务描述，回车即开始渗透测试。'
                : '会话尚未开始——agent 的消息与工具调用会出现在这里。'
            }
          />
        )}
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
