// SchedModal：设置执行时机（sched-mask）。即时 / 定时 / 周期 + Cron 模板。
// 按 prototype.html 的 renderSched / openSched / saveSched 实现。

import { useEffect, useState, type ReactNode } from 'react'
import type { Task } from './db.ts'

export type SchedType = 'now' | 'once' | 'cron'

export interface SchedModalProps {
  open: boolean
  task: Task | null
  onClose: () => void
  onSave: (next: { sched: SchedType; next: string }) => void
}

const WARN_SVG = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4M12 17h.01" />
  </svg>
)

export function SchedModal({ open, task, onClose, onSave }: SchedModalProps) {
  const [type, setType] = useState<SchedType>(task?.sched ?? 'now')
  const [date, setDate] = useState('2026-09-10T22:00')
  const [cron, setCron] = useState('0 2 * * 1')

  useEffect(() => {
    if (open) {
      setType(task?.sched ?? 'now')
      setDate('2026-09-10T22:00')
      setCron('0 2 * * 1')
    }
  }, [open, task])

  if (!open) return null

  const target = task ? `${task.target}` : '当前任务'

  const handleSave = () => {
    let next = ''
    if (type === 'once') next = date.slice(5).replace('T', ' ')
    else if (type === 'cron') next = `周期 ${cron}`
    onSave({ sched: type, next })
  }

  return (
    <div className="proto-modal-mask on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="proto-modal" role="dialog" aria-label="设置执行时机" style={{ width: 600, display: 'flex', flexDirection: 'column' }}>
        <div className="proto-modal-head">
          <span className="proto-modal-title">设置执行时机</span>
          <button className="proto-icon-btn" type="button" onClick={onClose} title="关闭">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="proto-modal-body" style={{ flex: 1 }}>
          <div className="proto-f-row" style={{ marginBottom: 4 }}>
            <span className="proto-f-lb">任务</span>
            <input className="proto-f-inp" value={target} disabled style={{ color: 'var(--text-3)' }} />
          </div>
          <div className="proto-set-sec" style={{ marginTop: 4 }}>
            <div className="proto-set-h">执行时机</div>
            <div className="proto-sched-grid">
              <SchedCard id="now" type={type} setType={setType} title="即时执行" desc="保存后立即开始，人工可全程介入">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m13 2-2 9h6l-8 11 2-9H5l8-11z" /></svg>
              </SchedCard>
              <SchedCard id="once" type={type} setType={setType} title="定时执行" desc="指定时间启动一次，适合变更窗口">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.2 2" /></svg>
              </SchedCard>
              <SchedCard id="cron" type={type} setType={setType} title="周期执行" desc="按 Cron 重复，适合合规复测">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3.2-6.9" /><path d="M21 3v6h-6" /></svg>
              </SchedCard>
            </div>
            {type !== 'now' && (
              <div className="proto-sched-opts">
                {type === 'once' ? (
                  <>
                    <div className="proto-f-row">
                      <span className="proto-f-lb">启动时间</span>
                      <input className="proto-f-inp mono" type="datetime-local" value={date} onChange={(e) => setDate(e.target.value)} />
                    </div>
                    <div className="proto-f-row">
                      <span className="proto-f-lb">有效窗口</span>
                      <select className="proto-f-sel" defaultValue="启动后 72 小时内有效">
                        <option>启动后 72 小时内有效</option>
                        <option>启动后 24 小时内有效</option>
                        <option>不限 · 直到手动停止</option>
                      </select>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="proto-f-row">
                      <span className="proto-f-lb">Cron</span>
                      <input className="proto-f-inp mono" value={cron} onChange={(e) => setCron(e.target.value)} />
                      <span style={{ fontSize: 11, color: 'var(--text-3)' }}>每周一 02:00</span>
                    </div>
                    <div className="proto-chips">
                      <button type="button" className="proto-chip" onClick={() => setCron('0 2 * * *')}>每天 02:00</button>
                      <button type="button" className="proto-chip" onClick={() => setCron('0 3 * * 1')}>每周一 03:00</button>
                      <button type="button" className="proto-chip" onClick={() => setCron('0 2 1 * *')}>每月 1 日 02:00</button>
                    </div>
                    <div className="proto-f-row">
                      <span className="proto-f-lb">结束条件</span>
                      <select className="proto-f-sel" defaultValue="永不结束">
                        <option>永不结束</option>
                        <option>执行 12 次后停止</option>
                        <option>到 2027-09-07 停止</option>
                      </select>
                    </div>
                  </>
                )}
                <div className="proto-conf-sched-tip" style={{ background: 'rgba(245,166,35,.1)', borderColor: 'rgba(245,166,35,.35)' }}>
                  {WARN_SVG}
                  <span>无人值守触发时若遇到需审批动作，将按<b>调度策略</b>处理：低危自动放行、高危挂起等待，超时后自动拒绝并记入审计日志。可在「「设置 → 调度」」中调整。</span>
                </div>
              </div>
            )}
          </div>
          <div className="proto-set-sec">
            <div className="proto-set-h">无人值守与冲突</div>
            <div className="proto-f-row">
              <span className="proto-f-lb">遇审批</span>
              <select className="proto-f-sel" defaultValue="低危自动放行 · 高危挂起等待">
                <option>低危自动放行 · 高危挂起等待</option>
                <option>全部挂起等待</option>
                <option>低危自动放行 · 高危跳过并继续</option>
              </select>
            </div>
            <div className="proto-f-row">
              <span className="proto-f-lb">冲突处理</span>
              <select className="proto-f-sel" defaultValue="同一目标排队执行">
                <option>同一目标排队执行</option>
                <option>跳过本次</option>
                <option>并行执行</option>
              </select>
            </div>
            <div className="proto-f-row">
              <span className="proto-f-lb">失败通知</span>
              <button className="proto-switch on" type="button" />
              <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>执行失败或超时立即通知负责人</span>
            </div>
          </div>
        </div>
        <div className="proto-modal-foot">
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>定时任务在无人值守时按调度策略处理审批</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="proto-btn ghost" onClick={onClose}>取消</button>
            <button type="button" className="proto-btn primary" onClick={handleSave}>保存</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function SchedCard(props: {
  id: SchedType
  type: SchedType
  setType: (t: SchedType) => void
  title: string
  desc: string
  children: ReactNode
}) {
  return (
    <div className={'proto-sched-card' + (props.type === props.id ? ' on' : '')} onClick={() => props.setType(props.id)}>
      <div className="sc-t">{props.children}{props.title}</div>
      <div className="sc-d">{props.desc}</div>
    </div>
  )
}