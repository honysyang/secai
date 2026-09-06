// ApprovalCard：人工审批卡片（视觉对齐 dsh ui-conversation ApprovalPanel：
// 琥珀「等待审批」条 + 理由标题 + 命令等宽行 + 右下 拒绝/允许 钮）。
// 数据 = approval/requested 帧登记的 PendingApprovalView；允许/拒绝 +
// 可选备注，应答走 props.onRespond（demo 本地裁决 / 联调 POST /api/respond）。
// 提交后本地 busy 防重，resolved 帧到达后卡片随快照消失。

import { useState } from 'react'
import type { ApprovalRequest } from '../../connection/api.ts'
import { formatClock } from '../../runtime/format.ts'
import { Input } from '../primitives/Input.tsx'
import css from './ApprovalCard.module.css'

export type RespondDecision = 'allow' | 'deny'

export interface ApprovalCardProps {
  rpcId: string
  payload: ApprovalRequest
  onRespond: (rpcId: string, decision: RespondDecision, comment?: string) => void
}

export function ApprovalCard({ rpcId, payload, onRespond }: ApprovalCardProps) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const detail = payload.detail !== undefined ? JSON.stringify(payload.detail, null, 2) : ''

  const decide = (decision: RespondDecision): void => {
    if (busy) return
    setBusy(true)
    const trimmed = comment.trim()
    onRespond(rpcId, decision, trimmed === '' ? undefined : trimmed)
  }

  return (
    <div className={css.root} role="alertdialog" aria-label={`审批：${payload.action}`}>
      <div className={css.card}>
        <div className={css.strip}>
          <span className={css.dot} aria-hidden="true" />
          等待人工审批
          <span className={css.time}>{formatClock(payload.createdAt)}</span>
        </div>
        <div className={css.body} data-approval-scroll tabIndex={0} role="group" aria-label="审批详情">
          <div className={css.headline}>{payload.description}</div>
          <div className={css.command}>{payload.action}</div>
          {detail !== '' && (
            <details className={css.details}>
              <summary className={css.detailsSummary}>请求参数</summary>
              <pre className={css.detail}>{detail}</pre>
            </details>
          )}
        </div>
        <div className={css.actionRow}>
          <Input
            size="sm"
            className={css.note}
            placeholder="备注（可选，随应答回传）"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            disabled={busy}
          />
          <button type="button" className={`${css.actionButton} ${css.reject}`} disabled={busy} onClick={() => decide('deny')}>
            拒绝
          </button>
          <button type="button" className={`${css.actionButton} ${css.allow}`} disabled={busy} onClick={() => decide('allow')}>
            允许
          </button>
        </div>
      </div>
    </div>
  )
}
