/** NewEngagementWizard：新建任务三步向导（目标与范围 → 任务参数 → 执行摘要）。 */

import { useState, useCallback } from 'react'
import type { TaskBrief } from '../../connection/api.ts'
import { Button } from '../primitives/Button.tsx'
import { Modal } from '../primitives/Modal.tsx'
import css from './NewEngagementWizard.module.css'

export interface NewEngagementWizardProps {
  open: boolean
  onClose: () => void
  onSubmit: (brief: TaskBrief) => Promise<void>
}

const PRESETS = [
  { label: '信息收集', hint: '端口扫描 + 指纹识别 + 目录枚举', text: '对目标进行全面的信息收集，包括端口扫描、服务指纹识别、目录枚举和 Web 技术栈识别。' },
  { label: '漏洞扫描', hint: 'CVE 匹配 + 弱点验证', text: '对目标进行漏洞扫描，匹配已知 CVE 并验证可利用性。' },
  { label: '横向移动', hint: '内网渗透 + 权限提升', text: '在已控主机基础上进行内网横向移动，寻找可提权路径。' },
  { label: '自定义', hint: '自由描述任务', text: '' },
]

export function NewEngagementWizard({ open, onClose, onSubmit }: NewEngagementWizardProps) {
  const [step, setStep] = useState(0)
  const [title, setTitle] = useState('')
  const [targets, setTargets] = useState('')
  const [taskPreset, setTaskPreset] = useState(0)
  const [taskText, setTaskText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const reset = useCallback(() => {
    setStep(0)
    setTitle('')
    setTargets('')
    setTaskPreset(0)
    setTaskText('')
    setError('')
  }, [])

  const handleClose = useCallback(() => {
    if (busy) return
    reset()
    onClose()
  }, [busy, reset, onClose])

  const canNext0 = targets.trim() !== ''
  const canNext1 = taskPreset < 3 || taskText.trim() !== ''

  const next = useCallback(() => {
    if (step === 0 && !canNext0) return
    if (step === 1) {
      if (!canNext1) return
      if (taskPreset < 3) setTaskText(PRESETS[taskPreset].text)
    }
    setError('')
    setStep((s) => s + 1)
  }, [step, canNext0, canNext1, taskPreset])

  const back = useCallback(() => {
    setError('')
    setStep((s) => s - 1)
  }, [])

  const submit = useCallback(async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setError('')
    const preset = PRESETS[taskPreset]
    const briefText = taskPreset < 3 ? preset.text : taskText.trim()
    // 任务描述合并进 title（后端 TaskBrief 无独立任务描述字段）
    const finalTitle = title.trim() !== ''
      ? `${title.trim()}：${briefText}`
      : briefText
    try {
      await onSubmit({
        title: finalTitle,
        allowedTargets: [targets.trim()],
      })
      reset()
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '任务书提交失败（详见控制台）')
    } finally {
      setBusy(false)
    }
  }, [busy, title, targets, taskPreset, taskText, reset, onClose, onSubmit])

  const summaryTargets = targets.trim().split('\n').filter(Boolean).slice(0, 3)

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="新建任务"
      contentClassName={css.content}
      footer={
        <div className={css.footerRow}>
          {step > 0 && (
            <Button variant="ghost" onClick={back} disabled={busy}>上一步</Button>
          )}
          <span style={{ flex: 1 }} />
          <Button variant="outline" onClick={handleClose} disabled={busy}>取消</Button>
          {step < 2 ? (
            <Button variant="primary" onClick={next} disabled={busy}>下一步</Button>
          ) : (
            <Button variant="primary" onClick={() => void submit()} disabled={busy}>
              {busy ? '提交中…' : '下发任务书'}
            </Button>
          )}
        </div>
      }
    >
      <div className={css.body}>
        {/* 步骤指示器 */}
        <div className={css.steps}>
          {['目标与范围', '任务参数', '执行摘要'].map((label, i) => (
            <div key={label} className={css.stepItem}>
              <div className={`${css.stepDot} ${i < step ? css.done : ''} ${i === step ? css.active : ''}`}>
                {i < step ? '✓' : i + 1}
              </div>
              <span className={`${css.stepLabel} ${i === step ? css.activeLabel : ''}`}>{label}</span>
              {i < 2 && <div className={css.stepLine} />}
            </div>
          ))}
        </div>

        {/* 步骤 0：目标与范围 */}
        {step === 0 && (
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
              <span className={css.label}>目标 / 范围</span>
              <textarea
                className={`${css.input} ${css.targets}`}
                value={targets}
                onChange={(e) => setTargets(e.target.value)}
                placeholder={'每行一个目标，支持 IP / CIDR / 域名 / 主机名，例如：\n192.168.56.0/24\ndemo.ine.local'}
                rows={3}
                disabled={busy}
              />
            </label>
          </div>
        )}

        {/* 步骤 1：任务参数 */}
        {step === 1 && (
          <div className={css.form}>
            <div className={css.field}>
              <span className={css.label}>任务模板</span>
              <div className={css.presetRow}>
                {PRESETS.map((p, i) => (
                  <button
                    key={p.label}
                    type="button"
                    className={`${css.presetChip} ${i === taskPreset ? css.presetActive : ''}`}
                    onClick={() => { setTaskPreset(i); if (i < 3) setTaskText(p.text) }}
                    disabled={busy}
                  >
                    <span className={css.presetName}>{p.label}</span>
                    <span className={css.presetHint}>{p.hint}</span>
                  </button>
                ))}
              </div>
            </div>
            <label className={css.field}>
              <span className={css.label}>任务描述</span>
              <textarea
                className={`${css.input} ${css.targets}`}
                value={taskText}
                onChange={(e) => { setTaskText(e.target.value); setTaskPreset(3) }}
                rows={4}
                disabled={busy}
              />
            </label>
          </div>
        )}

        {/* 步骤 2：执行摘要 */}
        {step === 2 && (
          <div className={css.summary}>
            <div className={css.summarySection}>
              <div className={css.summaryTitle}>目标</div>
              {summaryTargets.map((t) => (
                <div key={t} className={css.summaryRow}>{t}</div>
              ))}
            </div>
            <div className={css.summarySection}>
              <div className={css.summaryTitle}>任务</div>
              <div className={css.summaryRow}>{taskText}</div>
            </div>
          </div>
        )}

        {error !== '' && <div className={css.error} role="alert">{error}</div>}
      </div>
    </Modal>
  )
}
