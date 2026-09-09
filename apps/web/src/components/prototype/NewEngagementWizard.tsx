// NewEngagementWizard：新建任务三步向导（目标与范围 → 任务参数 → 确认）。
// 按 prototype.html 的 mStep/renderStep 复刻。

import { useEffect, useRef, useState } from 'react'

export interface NewTaskConfig {
  name: string
  target: string
  mode: 'pt' | 'scan' | 'code' | 'ctf' | 're' | 'assess'
  sched: 'now' | 'once' | 'cron'
  next: string
}

export interface NewEngagementWizardProps {
  open: boolean
  onClose: () => void
  onCreate: (cfg: NewTaskConfig) => void
}

const MODE_NAMES: Record<NewTaskConfig['mode'], string> = {
  pt: '渗透测试',
  scan: '漏洞扫描',
  code: '代码审计',
  ctf: 'CTF 竞赛',
  re: '二进制逆向',
  assess: '安全评估',
}

function validCIDR(s: string): boolean {
  return /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$|^[a-z0-9.-]+\.[a-z]{2,}$/i.test(s.trim())
}

export function NewEngagementWizard({ open, onClose, onCreate }: NewEngagementWizardProps) {
  const [step, setStep] = useState(1)
  const [scope, setScope] = useState<string[]>([])
  const [intensity, setIntensity] = useState<'快速' | '标准' | '深度'>('标准')
  const [opMode, setOpMode] = useState<NewTaskConfig['mode']>('pt')
  const [sched, setSched] = useState<NewTaskConfig['sched']>('now')
  const [date, setDate] = useState('2026-09-10T22:00')
  const [brief, setBrief] = useState('')
  const [wizAtts, setWizAtts] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setStep(1)
      setScope([])
      setIntensity('标准')
      setOpMode('pt')
      setSched('now')
      setBrief('')
    }
  }, [open])

  if (!open) return null

  const addScope = () => {
    const inp = inputRef.current
    if (!inp) return
    const v = inp.value.trim()
    if (!v) return
    if (!validCIDR(v)) return
    if (!scope.includes(v)) setScope([...scope, v])
    inp.value = ''
  }

  const handleNext = () => {
    if (step === 1 && scope.filter(validCIDR).length === 0) return
    if (step < 3) { setStep(step + 1); return }
    const t = scope.filter(validCIDR)[0]
    const next = sched === 'now' ? '' : sched === 'once' ? date.slice(5).replace('T', ' ') : `周期 ${date}`
    const name = brief.trim() ? `${t} — ${brief.trim()}` : `${t} — ${MODE_NAMES[opMode]}`
    onCreate({ name, target: t, mode: opMode, sched, next })
  }

  return (
    <div className="proto-modal-mask on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="proto-modal" role="dialog" aria-label="新建渗透任务">
        <div className="proto-modal-head">
          <span className="proto-modal-title">新建渗透任务</span>
          <button className="proto-icon-btn" type="button" onClick={onClose} title="关闭">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="proto-steps-bar">
          <div className={'proto-step' + (step === 1 ? ' on' : '') + (step > 1 ? ' done' : '')}><span className="n">1</span>授权范围</div>
          <div className={'proto-step' + (step === 2 ? ' on' : '') + (step > 2 ? ' done' : '')}><span className="n">2</span>目标与强度</div>
          <div className={'proto-step' + (step === 3 ? ' on' : '')}><span className="n">3</span>确认</div>
        </div>
        <div className="proto-modal-body">
          {step === 1 && (
            <>
              <div className="proto-f-label">作战模式<span className="opt">决定推理策略、工具集与产出模板</span></div>
              <div className="proto-sched-grid">
                {(Object.keys(MODE_NAMES) as Array<NewTaskConfig['mode']>).map((id) => (
                  <div key={id} className={'proto-sched-card' + (opMode === id ? ' on' : '')} onClick={() => setOpMode(id)}>
                    <div className="sc-t">{MODE_NAMES[id]}</div>
                    <div className="sc-d">{OP_DESC[id]}</div>
                  </div>
                ))}
              </div>
              <div className="proto-f-label" style={{ marginTop: 16 }}>授权目标（IP / CIDR / 域名）<span className="opt">授权即法律边界</span></div>
              <div className="proto-scope-list">
                {scope.map((s, i) => (
                  <div key={i} className={'proto-scope-item' + (validCIDR(s) ? ' ok' : ' bad')}>
                    <span className={'proto-dot ' + (validCIDR(s) ? 'run' : 'fail')} style={{ width: 6, height: 6 }} />
                    <span className="sv">{s}</span>
                    <span className="verdict">{validCIDR(s) ? '格式合法' : '格式非法'}</span>
                    <button type="button" className="proto-icon-btn" style={{ width: 24, height: 24 }} onClick={() => setScope(scope.filter((_, j) => j !== i))}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                    </button>
                  </div>
                ))}
              </div>
              <div className="proto-scope-add">
                <input ref={inputRef} className="proto-f-input mono" placeholder="例如 10.0.8.0/24 或 target.example.com" onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addScope() } }} />
                <button type="button" className="proto-btn ghost" onClick={addScope}>添加</button>
              </div>
              <div className="proto-chips" style={{ marginTop: 8 }}>
                <button type="button" className="proto-chip" onClick={() => setScope(['10.0.8.0/24'])}>单网段</button>
                <button type="button" className="proto-chip" onClick={() => setScope(['target.example.com'])}>单域名</button>
                <button type="button" className="proto-chip" onClick={() => setScope(['10.0.0.0/8'])}>大类内网</button>
              </div>
              <div className="proto-f-label" style={{ marginTop: 16 }}>授权材料（可选）<span className="opt">点击或拖拽上传授权书 / 目标清单 / 字典</span></div>
              <label
 className="proto-up-zone"
 onDragOver={(e) => { e.preventDefault() }}
 onDrop={(e) => {
   e.preventDefault()
   const files = Array.from(e.dataTransfer.files)
   setWizAtts((prev) => [...prev, ...files.map((f) => f.name)])
 }}
 >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" /></svg>
                <span>点击或拖拽上传文件</span>
                <span style={{ fontSize: 11, opacity: 0.7 }}>.txt .csv .pdf .png .jpg .pcap .har（单文件 ≤ 20MB）</span>
                <input type="file" multiple hidden onChange={(e) => {
                  const files = Array.from(e.target.files ?? [])
                  setWizAtts((prev) => [...prev, ...files.map((f) => f.name)])
                  e.target.value = ''
                }} />
              </label>
              {wizAtts.length > 0 && (
                <div className="proto-wiz-atts">
                  {wizAtts.map((name, i) => (
                    <span key={i} className="proto-att-chip">{name}<button type="button" onClick={() => setWizAtts(wizAtts.filter((_, j) => j !== i))}>×</button></span>
                  ))}
                </div>
              )}
              <div className="proto-warn-strip">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2" strokeLinecap="round" style={{ flex: 'none', marginTop: 1 }}><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" /></svg>
                <span>智能体将<b>拒绝</b>对范围外目标的一切操作；排除项与时间窗可在高级设置中进一步收窄。所有动作默认留存审计日志。</span>
              </div>
            </>
          )}
          {step === 2 && (
            <>
              <div className="proto-f-label">任务目标（可选）<span className="opt">留空则由智能体按标准流程推进</span></div>
              <textarea className="proto-f-area" placeholder="例如：重点关注登录与文件上传功能，产出中文报告；跳过 DoS 类测试。" value={brief} onChange={(e) => setBrief(e.target.value)} />
              <div className="proto-f-label">强度档位</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                {(['快速', '标准', '深度'] as const).map((v) => (
                  <div key={v} className={'proto-sched-card' + (intensity === v ? ' on' : '')} onClick={() => setIntensity(v)}>
                    <div className="sc-t">{v}</div>
                    <div className="sc-d">{INTENSITY_DESC[v]}</div>
                  </div>
                ))}
              </div>
              <div className="proto-f-label" style={{ marginTop: 16 }}>执行时机<span className="opt">决定任务何时启动</span></div>
              <div className="proto-sched-grid">
                <div className={'proto-sched-card' + (sched === 'now' ? ' on' : '')} onClick={() => setSched('now')}>
                  <div className="sc-t">即时执行</div>
                  <div className="sc-d">确认后立即开始，人工可全程介入</div>
                </div>
                <div className={'proto-sched-card' + (sched === 'once' ? ' on' : '')} onClick={() => setSched('once')}>
                  <div className="sc-t">定时执行</div>
                  <div className="sc-d">指定时间启动一次，适合变更窗口</div>
                </div>
                <div className={'proto-sched-card' + (sched === 'cron' ? ' on' : '')} onClick={() => setSched('cron')}>
                  <div className="sc-t">周期执行</div>
                  <div className="sc-d">按 Cron 重复，适合合规复测</div>
                </div>
              </div>
              {sched !== 'now' && (
                <div style={{ marginTop: 12 }}>
                  <input type={sched === 'once' ? 'datetime-local' : 'text'} className="proto-f-input mono" value={date} onChange={(e) => setDate(e.target.value)} />
                </div>
              )}
            </>
          )}
          {step === 3 && (
            <div className="proto-confirm-box">
              <div className="proto-kv"><span className="k">授权范围</span><span className="v">{scope.join('、')}</span></div>
              <div className="proto-kv"><span className="k">强度档位</span><span className="v">{intensity}</span></div>
              <div className="proto-kv"><span className="k">作战模式</span><span className="v">{MODE_NAMES[opMode]}</span></div>
              <div className="proto-kv"><span className="k">执行时机</span><span className="v">{sched === 'now' ? '即时执行' : sched === 'once' ? date.replace('T', ' ') : `周期 ${date}`}</span></div>
              <div className="proto-kv"><span className="k">任务目标</span><span className="v">{brief.trim() || '未设置（标准流程）'}</span></div>
              <div className="proto-kv"><span className="k">预计动作</span><span className="v">40-60 次工具调用 · 3-5 次人工审批</span></div>
              <div className="proto-kv"><span className="k">禁用动作</span><span className="v">DoS · 社工 · 数据破坏（默认）</span></div>
            </div>
          )}
        </div>
        <div className="proto-modal-foot">
          <button type="button" className="proto-btn ghost" onClick={() => { if (step > 1) setStep(step - 1); else onClose() }} style={{ visibility: step === 1 ? 'hidden' : 'visible' }}>
            {step === 1 ? '取消' : '上一步'}
          </button>
          <button type="button" className="proto-btn primary" onClick={handleNext} disabled={step === 1 && scope.filter(validCIDR).length === 0}>
            {step === 3 ? '创建任务' : '下一步'}
          </button>
        </div>
      </div>
    </div>
  )
}

const OP_DESC: Record<NewTaskConfig['mode'], string> = {
  pt: '授权目标 · nmap/nuclei/Metasploit/手工验证',
  scan: '目标清单 · nmap/nuclei/ffuf/hydra',
  code: '代码仓库 · Semgrep/CodeQL/Joern',
  ctf: '题目附件 + 描述 · pwntools/z3/binwalk',
  re: '样本文件 · Ghidra/radare2/Frida/沙箱',
  assess: '评估范围 + 合规框架 · 全量工具链',
}

const INTENSITY_DESC: Record<'快速' | '标准' | '深度', string> = {
  快速: 'Top 端口 + 高置信漏洞，约 30 分钟',
  标准: '全端口 + 目录枚举 + 凭据验证',
  深度: '含横向评估与利用链验证，T3 审批增多',
}