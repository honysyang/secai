// NewEngagementModal：「新建任务」任务书表单弹窗（Modal 原语 + dsh 表单语言）。
// 字段 = title（可空）+ 目标描述（自由文本，任意语言——IP / CIDR / 域名 /
// 主机名 / 自然语言任务描述均可，整段作为一个授权目标下发）。不再提供
// maxIntensity 强度选择（由后端策略决定）。busy 防重，error 行内呈现，
// 成功后由父级关窗。

import { useState } from 'react'
import type { TaskBrief } from '../../connection/api.ts'
import { Button } from '../primitives/Button.tsx'
import { Modal } from '../primitives/Modal.tsx'
import css from './NewEngagementModal.module.css'

export interface NewEngagementModalProps {
  open: boolean
  onClose: () => void
  onSubmit: (brief: TaskBrief) => Promise<void>
}

export function NewEngagementModal({ open, onClose, onSubmit }: NewEngagementModalProps) {
  const [title, setTitle] = useState('')
  const [targets, setTargets] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const canSubmit = targets.trim() !== '' && !busy

  const submit = async (): Promise<void> => {
    if (!canSubmit) return
    setBusy(true)
    setError('')
    try {
      await onSubmit({
        ...(title.trim() !== '' ? { title: title.trim() } : {}),
        allowedTargets: [targets.trim()],
      })
      // 成功：清空表单并关窗（失败则保留输入，行内报错）
      setTitle('')
      setTargets('')
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '任务书提交失败（详见控制台）')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose() }}
      title="新建任务"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>取消</Button>
          <Button variant="primary" onClick={() => void submit()} disabled={!canSubmit}>
            {busy ? '提交中…' : '下发任务书'}
          </Button>
        </>
      }
    >
      <div className={css.form}>
        <label className={css.field}>
          <span className={css.label}>任务标题（可选）</span>
          <input
            className={css.input}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例如：demo.ine.local 集群侦察与提权"
            disabled={busy}
          />
        </label>

        <label className={css.field}>
          <span className={css.label}>目标 / 任务描述</span>
          <textarea
            className={`${css.input} ${css.targets}`}
            value={targets}
            onChange={(e) => setTargets(e.target.value)}
            placeholder={'支持任意语言描述目标，例如：\n192.168.56.0/24\ndemo.ine.local\n帮我渗透测试内网 10.10.5.0/24 网段，寻找可提权主机'}
            rows={3}
            disabled={busy}
          />
        </label>

        {error !== '' && <div className={css.error} role="alert">{error}</div>}
      </div>
    </Modal>
  )
}
