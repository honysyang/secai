// NewEngagementModal：「新建任务」任务书表单弹窗（Modal 原语 + dsh 表单语言）。
// 字段 = title（可空）+ allowedTargets（逗号/空格分隔的 CIDR/域名清单）+
// maxIntensity（passive/active/aggressive）。提交调 props.onSubmit（App 层走
// runtime.run → api.call('run')）；busy 防重，error 行内呈现，成功后由父级关窗。

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

const INTENSITY_OPTIONS: ReadonlyArray<{ value: NonNullable<TaskBrief['maxIntensity']>; label: string; hint: string }> = [
  { value: 'passive', label: 'passive 被动', hint: '只读探测：端口识别、banner 抓取，不产生攻击流量' },
  { value: 'active', label: 'active 主动', hint: '标准验证：登录尝试、漏洞利用 PoC（默认强度）' },
  { value: 'aggressive', label: 'aggressive 激进', hint: '高强度：暴力枚举、泛洪式测试——仅在获授权范围使用' },
]

/** 目标清单解析：逗号/空格/换行分隔 → 去空去重。 */
function parseTargets(raw: string): string[] {
  const seen = new Set<string>()
  for (const token of raw.split(/[\s,，;；]+/)) {
    const item = token.trim()
    if (item !== '') seen.add(item)
  }
  return [...seen]
}

export function NewEngagementModal({ open, onClose, onSubmit }: NewEngagementModalProps) {
  const [title, setTitle] = useState('')
  const [targets, setTargets] = useState('')
  const [intensity, setIntensity] = useState<NonNullable<TaskBrief['maxIntensity']>>('active')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const targetList = parseTargets(targets)
  const canSubmit = targetList.length > 0 && !busy

  const submit = async (): Promise<void> => {
    if (!canSubmit) return
    setBusy(true)
    setError('')
    try {
      await onSubmit({
        ...(title.trim() !== '' ? { title: title.trim() } : {}),
        allowedTargets: targetList,
        maxIntensity: intensity,
      })
      // 成功：清空表单并关窗（失败则保留输入，行内报错）
      setTitle('')
      setTargets('')
      setIntensity('active')
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
          <span className={css.label}>
            允许目标（CIDR / 域名，逗号或空格分隔）
            <span className={css.count}>{targetList.length > 0 ? `已识别 ${targetList.length} 个` : ''}</span>
          </span>
          <textarea
            className={`${css.input} ${css.targets}`}
            value={targets}
            onChange={(e) => setTargets(e.target.value)}
            placeholder={'192.168.56.0/24\ndemo.ine.local'}
            rows={3}
            disabled={busy}
          />
        </label>

        <div className={css.field}>
          <span className={css.label}>最大强度（maxIntensity）</span>
          <div className={css.intensityRow} role="radiogroup" aria-label="最大强度">
            {INTENSITY_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={intensity === option.value}
                className={`${css.intensity}${intensity === option.value ? ` ${css.intensityActive}` : ''}`}
                onClick={() => setIntensity(option.value)}
                disabled={busy}
              >
                <span className={css.intensityLabel}>{option.label}</span>
                <span className={css.intensityHint}>{option.hint}</span>
              </button>
            ))}
          </div>
        </div>

        {error !== '' && <div className={css.error} role="alert">{error}</div>}
      </div>
    </Modal>
  )
}
