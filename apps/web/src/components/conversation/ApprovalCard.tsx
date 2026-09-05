/**
 * ApprovalCard：人工审批卡片（F3）。数据 = approval/requested 帧登记的
 * PendingApprovalView（payload + 帧级 rpcId）。允许/拒绝 + 备注；应答走
 * props.onRespond → 上层（demo 本地裁决并广播 resolution / 联调时
 * POST /api/respond 回响 rpcId）。提交后本地 busy 防重。
 */

import { useState } from 'react'
import type { ApprovalRequest } from '../../connection/api.ts'
import { formatClock } from '../../runtime/format.ts'
import { Button } from '../primitives/Button.tsx'
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
    <div className={css.card} role="alertdialog" aria-label={`审批：${payload.action}`}>
      <div className={css.head}>
        <span className={css.pulse} aria-hidden="true" />
        <span className={css.title}>{payload.action}</span>
        <span className={css.time}>{formatClock(payload.createdAt)}</span>
      </div>
      <p className={css.desc}>{payload.description}</p>
      {detail !== '' && (
        <details className={css.details}>
          <summary className={css.detailsSummary}>请求参数</summary>
          <pre className={css.detail}>{detail}</pre>
        </details>
      )}
      <div className={css.actions}>
        <Input
          size="sm"
          className={css.note}
          placeholder="备注（可选，随应答回传）"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          disabled={busy}
        />
        <Button size="sm" variant="outline" className={css.deny} disabled={busy} onClick={() => decide('deny')}>
          拒绝
        </Button>
        <Button size="sm" variant="info" disabled={busy} onClick={() => decide('allow')}>
          允许
        </Button>
      </div>
    </div>
  )
}
