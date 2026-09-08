// SettingsModal：设置中心 5 tab（模型 / 审批策略 / 调度 / 用户与权限 / 通用）。
// 按 prototype.html 的 set-mask / renderSet / saveSettings 实现：
//   - DB.settings / DB.rules / DB.members / DB.apis 全部持久化到 localStorage
//   - 通过 setSettings/setRules/setMembers/setApis 回调写入
//   - 5 tab 切换：模型/审批/调度/用户/通用
//
// DOM 复用：使用与 NewEngagementWizard 同款 .proto-modal-mask/.proto-modal 样式，
// 内容区 .proto-set-tabs 顶部 tab 行 + .proto-modal-body 内部 .proto-set-sec 节段。

import { useEffect, useState } from 'react'
import type { DBShape, Settings, Rule, Member, ApiKey } from './db.ts'

export interface SettingsModalProps {
  open: boolean
  initialTab?: SettingsTab
  db: DBShape
  onClose: () => void
  onSave: (patch: {
    settings?: Partial<Settings>
    rules?: Rule[]
    members?: Member[]
    apis?: ApiKey[]
  }) => void
}

export type SettingsTab = 'model' | 'approval' | 'sched' | 'users' | 'general'

const TABS: Array<{ id: SettingsTab; label: string }> = [
  { id: 'model', label: '模型' },
  { id: 'approval', label: '审批策略' },
  { id: 'sched', label: '调度' },
  { id: 'users', label: '用户与权限' },
  { id: 'general', label: '通用' },
]

const APPR_TIERS: Array<{ id: Settings['apprMode']; t: string; d: string }> = [
  { id: '严格', t: '严格', d: '利用与横向动作全部需人工确认' },
  { id: '标准', t: '标准', d: '信息收集自动放行，利用与横向需审批' },
  { id: '自主', t: '自主', d: '仅破坏性动作拦截，其余自动放行' },
]

const TIMEOUT_OPTIONS = ['超时 10 分钟自动拒绝', '超时自动放行', '持续挂起等待']

const ROLE_CLASS: Record<Member['rc'], string> = {
  'r-admin': 'r-admin',
  'r-eng': 'r-eng',
  'r-appr': 'r-appr',
  'r-audit': 'r-audit',
}

export function SettingsModal({ open, initialTab = 'model', db, onClose, onSave }: SettingsModalProps) {
  const [tab, setTab] = useState<SettingsTab>(initialTab)
  const [settings, setSettings] = useState<Settings>(db.settings)
  const [rules, setRules] = useState<Rule[]>(db.rules)
  const [members, setMembers] = useState<Member[]>(db.members)
  const [apis, setApis] = useState<ApiKey[]>(db.apis)

  useEffect(() => {
    if (open) {
      setTab(initialTab)
      setSettings(db.settings)
      setRules(db.rules)
      setMembers(db.members)
      setApis(db.apis)
    }
  }, [open, initialTab, db.settings, db.rules, db.members, db.apis])

  if (!open) return null

  const handleSave = () => {
    onSave({ settings, rules, members, apis })
    onClose()
  }

  return (
    <div className="proto-modal-mask on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="proto-modal" role="dialog" aria-label="设置" style={{ width: 780, display: 'flex', flexDirection: 'column' }}>
        <div className="proto-modal-head">
          <span className="proto-modal-title">设置</span>
          <button className="proto-icon-btn" type="button" onClick={onClose} title="关闭">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="proto-set-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={'proto-set-tab' + (tab === t.id ? ' on' : '')}
              type="button"
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="proto-modal-body" style={{ flex: 1 }}>
          {tab === 'model' && <ModelTab settings={settings} setSettings={setSettings} />}
          {tab === 'approval' && <ApprovalTab setSettings={setSettings} settings={settings} rules={rules} setRules={setRules} />}
          {tab === 'sched' && <SchedTab />}
          {tab === 'users' && <UsersTab members={members} setMembers={setMembers} apis={apis} setApis={setApis} />}
          {tab === 'general' && <GeneralTab />}
        </div>
        <div className="proto-modal-foot">
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>全局默认配置 · 单个任务可在新建时覆盖</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="proto-btn ghost" onClick={onClose}>取消</button>
            <button type="button" className="proto-btn primary" onClick={handleSave}>保存</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ============ Model Tab ============ */

function ModelTab({ settings, setSettings }: { settings: Settings; setSettings: (s: Settings) => void }) {
  return (
    <>
      <div className="proto-set-sec">
        <div className="proto-set-h">推理服务</div>
        <div className="proto-set-d">支持本地 llama.cpp / vLL 与云端 API。涉密目标建议本地部署，避免任务数据与凭据外发。</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">服务地址</span>
          <input className="proto-f-inp mono" value={settings.endpoint} onChange={(e) => setSettings({ ...settings, endpoint: e.target.value })} />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">模型</span>
          <input className="proto-f-inp mono" value={settings.model} onChange={(e) => setSettings({ ...settings, model: e.target.value })} />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">并发数</span>
          <input className="proto-f-inp" type="number" value={settings.concurrency} style={{ maxWidth: 90 }} onChange={(e) => setSettings({ ...settings, concurrency: parseInt(e.target.value) || 0 })} />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>上下文 128k · 实测 62 t/s · 显存占用 18.6 GB</span>
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">角色模型</div>
        <div className="proto-set-d">按环节分配模型：规划要推理力，执行要速度与工具调用稳定，报告要中文表达。此处为全局默认，各作战模式另有推荐配置，切换模式时会提示应用。</div>
        <div className="proto-role-grid">
          <RoleCard title="规划 Planner" tag="策略与任务分解" model="Qwen3-30B-A3B（本地）" temp="0.3" extra="低温更稳定" />
          <RoleCard title="执行 Executor" tag="工具调用与判读" model="Qwen3-30B-A3B（本地）" temp="0.1" extra="近确定性" />
          <RoleCard title="分析 Analyst" tag="证据归纳与报告" model="Qwen3-30B-A3B（本地）" temp="0.4" extra="中文长文本" />
          <RoleCard title="嵌入 Embedding" tag="知识库检索" model="bge-m3（本地）" temp="—" extra="1024 维" />
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">护栏</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">单次超时</span>
          <input className="proto-f-inp" value={settings.runTimeout} style={{ maxWidth: 100 }} onChange={(e) => setSettings({ ...settings, runTimeout: e.target.value })} />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">失败重试</span>
          <input className="proto-f-inp" type="number" value={settings.retries} style={{ maxWidth: 90 }} onChange={(e) => setSettings({ ...settings, retries: parseInt(e.target.value) || 0 })} />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>同一工具连续失败 3 次则转人工</span>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">脱敏外发</span>
          <button className={'proto-switch' + (settings.desens ? ' on' : '')} type="button" onClick={() => setSettings({ ...settings, desens: !settings.desens })} />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>使用云端模型时自动剥离目标 IP、域名与凭据</span>
        </div>
      </div>
    </>
  )
}

function RoleCard({ title, tag, model, temp, extra }: { title: string; tag: string; model: string; temp: string; extra: string }) {
  return (
    <div className="proto-role-card">
      <div className="proto-rc-h">{title}<span className="proto-rc-tag">{tag}</span></div>
      <div className="proto-f-row">
        <span className="proto-f-lb">模型</span>
        <select className="proto-f-sel" defaultValue={model}>
          <option>{model}</option>
          <option>Qwen3-80B-A3B（本地 · 更强推理）</option>
          <option>GPT-4o（云端 API）</option>
          <option>Claude Sonnet（云端 API）</option>
        </select>
      </div>
      <div className="proto-f-row">
        <span className="proto-f-lb">{temp === '—' ? '维度' : '温度'}</span>
        <input className="proto-f-inp" defaultValue={temp} style={{ maxWidth: 88 }} />
        <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{extra}</span>
      </div>
    </div>
  )
}

/* ============ Approval Tab ============ */

const RULE_DECISION_CLASS: Record<Rule['d'], string> = {
  '自动放行': 'b-auto',
  '人工审批': 'b-appr',
  '禁止': 'b-deny',
}

function ApprovalTab({ settings, setSettings, rules, setRules }: {
  settings: Settings
  setSettings: (s: Settings) => void
  rules: Rule[]
  setRules: (r: Rule[]) => void
}) {
  const addRule = () => {
    const r: Rule = { a: '自定义动作 · 待命名', c: '任何条件', d: '人工审批', s: '全部任务' }
    setRules([...rules.slice(0, 4), r, ...rules.slice(4)])
  }
  const delRule = (i: number) => setRules(rules.filter((_, j) => j !== i))
  return (
    <>
      <div className="proto-set-sec">
        <div className="proto-set-h">自动化审批档位</div>
        <div className="proto-set-d">决定智能体无人值守时的自主程度。高危动作始终受下方规则表约束，档位只能放宽低危部分。</div>
        <div className="proto-mode-grid" id="appr-grid">
          {APPR_TIERS.map((tier) => (
            <div
              key={tier.id}
              className={'proto-mode-card' + (settings.apprMode === tier.id ? ' on' : '')}
              data-tier={tier.id}
              onClick={() => setSettings({ ...settings, apprMode: tier.id })}
            >
              <div className="mc-t">{tier.t}</div>
              <div className="mc-d">{tier.d}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">动作规则</div>
        <div className="proto-set-d">自上而下匹配，命中第一条即生效。审批卡上勾选「自动放行同类动作」会在此追加一条规则。</div>
        <div className="proto-rule-tb">
          <div className="proto-rt-head"><span>动作类型</span><span>触发条件</span><span>处置</span><span>生效范围</span><span></span></div>
          {rules.map((r, i) => (
            <div key={i} className="proto-rt-row">
              <span>{r.a}</span>
              <span style={{ color: 'var(--text-3)', fontSize: 11.5 }}>{r.c}</span>
              <span><span className={'proto-badge-mini ' + RULE_DECISION_CLASS[r.d]}>{r.d}</span></span>
              <span style={{ color: 'var(--text-3)', fontSize: 11.5 }}>{r.s}</span>
              <span className="proto-rt-act">
                <button type="button" className="proto-icon-btn" style={{ width: 22, height: 22 }} onClick={() => delRule(i)} title="删除规则">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                </button>
              </span>
            </div>
          ))}
        </div>
        <button type="button" className="proto-btn ghost" style={{ marginTop: 10 }} onClick={addRule}>+ 新增规则</button>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">审批流转</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">超时策略</span>
          <select className="proto-f-sel" value={settings.timeout} onChange={(e) => setSettings({ ...settings, timeout: e.target.value })}>
            {TIMEOUT_OPTIONS.map((o) => <option key={o}>{o}</option>)}
          </select>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">审批人</span>
          <input className="proto-f-inp" defaultValue="任务创建人 · T3 动作需双人复核" />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">通知</span>
          <button className="proto-switch on" type="button" />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>企业微信 / 邮件 / Webhook</span>
        </div>
      </div>
    </>
  )
}

/* ============ Sched Tab ============ */

function SchedTab() {
  return (
    <>
      <div className="proto-set-sec">
        <div className="proto-set-h">全局调度策略</div>
        <div className="proto-set-d">所有定时与周期任务遵循以下约束，单个任务可在「设置执行时机」中覆盖。</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">并发上限</span>
          <input className="proto-f-inp" type="number" defaultValue={2} style={{ maxWidth: 90 }} />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>同时运行的自动任务数，超出则排队</span>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">执行时间窗</span>
          <select className="proto-f-sel" defaultValue="仅允许 00:00-06:00 执行">
            <option>仅允许 00:00-06:00 执行</option>
            <option>仅工作日 22:00-06:00</option>
            <option>不限</option>
          </select>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">时区</span>
          <select className="proto-f-sel" defaultValue="Asia/Shanghai (UTC+8)">
            <option>Asia/Shanghai (UTC+8)</option>
            <option>UTC</option>
          </select>
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">无人值守审批</div>
        <div className="proto-set-d">定时触发时通常无人在线，需明确每个风险等级的默认处置方式。</div>
        <div className="proto-rule-tb">
          <div className="proto-rt-head"><span>风险等级</span><span>默认处置</span><span>超时</span><span>通知</span><span></span></div>
          <SchedRow level="T1 · 低危" decision="自动放行" cls="b-auto" timeout="—" notify="仅异常时" />
          <SchedRow level="T2 · 中危" decision="挂起等待" cls="b-appr" timeout="10 分钟" notify="通知负责人" />
          <SchedRow level="T3 · 高危" decision="挂起 + 双人" cls="b-deny" timeout="30 分钟" notify="通知 + 升级" />
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">执行记录</div>
        <div className="proto-set-d">最近一次：10.0.8.0/24 月度合规复测 · 09-05 02:00 成功 · 用时 41 分钟 · 发现 3 个中危</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">保留时长</span>
          <select className="proto-f-sel" defaultValue="保留 90 天执行记录">
            <option>保留 90 天执行记录</option>
            <option>保留 365 天</option>
          </select>
        </div>
      </div>
    </>
  )
}

function SchedRow({ level, decision, cls, timeout, notify }: { level: string; decision: string; cls: string; timeout: string; notify: string }) {
  return (
    <div className="proto-rt-row">
      <span>{level}</span>
      <span><span className={'proto-badge-mini ' + cls}>{decision}</span></span>
      <span style={{ color: 'var(--text-3)', fontSize: 11.5 }}>{timeout}</span>
      <span style={{ color: 'var(--text-3)', fontSize: 11.5 }}>{notify}</span>
      <span className="proto-rt-act"></span>
    </div>
  )
}

/* ============ Users Tab ============ */

function UsersTab({ members, setMembers, apis, setApis }: {
  members: Member[]
  setMembers: (m: Member[]) => void
  apis: ApiKey[]
  setApis: (a: ApiKey[]) => void
}) {
  const toggleMember = (i: number) => {
    setMembers(members.map((m, j) => {
      if (j !== i) return m
      const on = m.on ? 0 : 1
      return { ...m, on, s: on ? '活跃' : '已禁用' }
    }))
  }
  const delMember = (i: number) => setMembers(members.filter((_, j) => j !== i))
  const revokeKey = (i: number) => {
    if (typeof window !== 'undefined' && !window.confirm(`确认吊销密钥 ${apis[i]?.n}？依赖该密钥的集成将立即失效。`)) return
    setApis(apis.filter((_, j) => j !== i))
  }
  return (
    <>
      <div className="proto-set-sec">
        <div className="proto-set-h">成员</div>
        <div className="proto-set-d">渗透平台涉及高权限动作，建议按最小化原则分配角色。T3 高危审批需两名具备权限的成员确认。</div>
        <div className="proto-mem-tb">
          <div className="proto-mem-head"><span>成员</span><span>角色</span><span>状态</span><span>最近活跃</span><span></span></div>
          {members.map((m, i) => (
            <div key={i} className="proto-mem-row">
              <span className="proto-mem-u">
                <span className="avatar">{m.n.slice(0, 1)}</span>
                <span><span className="mu-n">{m.n}</span><br /><span className="mu-m">{m.m}</span></span>
              </span>
              <span><span className={'proto-role-badge ' + ROLE_CLASS[m.rc]}>{m.r}</span></span>
              <span style={{ fontSize: 11.5, color: m.on ? 'var(--ok)' : 'var(--text-3)' }}>{m.s}</span>
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{m.t}</span>
              <span className="proto-rt-act">
                <button type="button" className="proto-badge-mini" style={{ background: 'none', border: '1px solid var(--border)', color: 'var(--text-3)', cursor: 'pointer' }} onClick={() => toggleMember(i)}>{m.on ? '停用' : '启用'}</button>
                <button type="button" className="proto-badge-mini b-deny" style={{ cursor: 'pointer', border: 'none' }} onClick={() => delMember(i)}>移除</button>
              </span>
            </div>
          ))}
        </div>
        <button type="button" className="proto-btn ghost" style={{ marginTop: 10 }} onClick={() => {
          const n = typeof window !== 'undefined' ? window.prompt('邀请成员：姓名') : ''
          if (!n) return
          const email = (typeof window !== 'undefined' ? window.prompt('邮箱', '') : '') || `${n.toLowerCase()}@corp.com`
          setMembers([...members, { n, m: email, r: '安全工程师', rc: 'r-eng', s: '活跃', on: 1, t: '刚刚' }])
        }}>+ 邀请成员</button>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">角色权限</div>
        <div className="proto-set-d">权限以角色为单位授予。审计账号仅有只读与日志权限，符合合规分离要求。</div>
        <div className="proto-perm-tb">
          <div className="proto-perm-head"><span>权限项</span><span>管理员</span><span>安全工程师</span><span>审批人</span><span>只读审计</span></div>
          <PermRow name="创建与执行任务" a={1} b={1} c={0} d={0} />
          <PermRow name="执行利用类动作" a={1} b={1} c={0} d={0} />
          <PermRow name="审批 T2 中危" a={1} b={0} c={1} d={0} />
          <PermRow name="审批 T3 高危（双人）" a={1} b={0} c={1} d={0} />
          <PermRow name="管理用户与角色" a={1} b={0} c={0} d={0} />
          <PermRow name="修改模型与审批策略" a={1} b={0} c={0} d={0} />
          <PermRow name="导出报告" a={1} b={1} c={1} d={1} />
          <PermRow name="查看审计日志" a={1} b={0} c={0} d={1} />
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">认证与安全</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">登录方式</span>
          <select className="proto-f-sel" defaultValue="企业 SSO + 本地密码兜底">
            <option>企业 SSO + 本地密码兜底</option>
            <option>仅企业 SSO</option>
            <option>仅本地密码</option>
            <option>LDAP / AD</option>
          </select>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">双因素</span>
          <button className="proto-switch on" type="button" />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>强制 TOTP，管理员与审批人必开</span>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">密码策略</span>
          <input className="proto-f-inp" defaultValue="≥ 12 位 · 含大小写数字符号 · 90 天轮换" />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">登录保护</span>
          <input className="proto-f-inp" defaultValue="失败 5 次锁定 15 分钟 · 会话 30 分钟无操作登出" style={{ fontSize: 11.5 }} />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">IP 白名单</span>
          <input className="proto-f-inp mono" defaultValue="10.0.0.0/8, 192.168.0.0/16" />
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">API 密钥</div>
        <div className="proto-set-d">用于CLI 与 CI 集成。密钥仅在创建时可见，建议按最小作用域签发并定期轮换。</div>
        <div className="proto-mem-tb">
          {apis.map((a, i) => (
            <div key={i} className="proto-mem-row" style={{ gridTemplateColumns: '1.3fr 1fr .9fr 1fr 54px' }}>
              <span className="mono">{a.n}</span>
              <span style={{ color: 'var(--text-3)', fontSize: 11.5 }}>{a.s}</span>
              <span style={{ color: 'var(--text-3)', fontSize: 11 }}>{a.d}</span>
              <span style={{ color: 'var(--text-3)', fontSize: 11 }}>{a.u}</span>
              <span className="proto-rt-act">
                <button type="button" className="proto-badge-mini b-deny" style={{ cursor: 'pointer', border: 'none' }} onClick={() => revokeKey(i)}>吊销</button>
              </span>
            </div>
          ))}
        </div>
        <button type="button" className="proto-btn ghost" style={{ marginTop: 10 }} onClick={() => {
          const nm = (typeof window !== 'undefined' ? window.prompt('密钥名称', 'secai-bot') : '') || ''
          if (!nm) return
          const scope = (typeof window !== 'undefined' ? window.prompt('作用域', '只读 · 报告') : '') || '只读 · 报告'
          const rand = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
          setApis([{ n: nm, s: scope, d: new Date().toISOString().slice(0, 10), u: '刚刚', key: 'sk-' + rand.slice(0, 24) }, ...apis])
        }}>+ 新建密钥</button>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">最近操作审计</div>
        <div className="proto-audit-list">
          <div className="proto-audit-item"><span className="ai-t">09-07 23:40</span><span><b>{members[0]?.n ?? '张伟'}</b> 修改了审批策略：T3 超时 30 → 60 分钟</span></div>
          <div className="proto-audit-item"><span className="ai-t">09-07 22:58</span><span><b>{members[2]?.n ?? '王强'}</b> 批准了动作 #0241（exploit/tomcat_put_rce）</span></div>
          <div className="proto-audit-item"><span className="ai-t">09-07 21:02</span><span><b>{members[1]?.n ?? '李娜'}</b> 创建了任务 demo.ine.local</span></div>
        </div>
      </div>
    </>
  )
}

function PermRow({ name, a, b, c, d }: { name: string; a: number; b: number; c: number; d: number }) {
  return (
    <div className="proto-perm-row">
      <span>{name}</span>
      <span className={a ? 'proto-mark-yes' : 'proto-mark-no'}>{a ? '✓' : '—'}</span>
      <span className={b ? 'proto-mark-yes' : 'proto-mark-no'}>{b ? '✓' : '—'}</span>
      <span className={c ? 'proto-mark-yes' : 'proto-mark-no'}>{c ? '✓' : '—'}</span>
      <span className={d ? 'proto-mark-yes' : 'proto-mark-no'}>{d ? '✓' : '—'}</span>
    </div>
  )
}

/* ============ General Tab ============ */

function GeneralTab() {
  return (
    <>
      <div className="proto-set-sec">
        <div className="proto-set-h">界面</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">语言</span>
          <select className="proto-f-sel" defaultValue="简体中文">
            <option>简体中文</option>
            <option>English</option>
          </select>
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">信息密度</span>
          <select className="proto-f-sel" defaultValue="标准">
            <option>标准</option>
            <option>紧凑</option>
          </select>
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">安全与合规</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">审计日志</span>
          <input className="proto-f-inp" defaultValue="保留 365 天 · 含完整工具输入输出" />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">命令黑名单</span>
          <input className="proto-f-inp mono" defaultValue="rm -rf, mkfs, dd, shutdown" />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">遥测</span>
          <button className="proto-switch" type="button" />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>关闭后不上传任何使用统计</span>
        </div>
      </div>
      <div className="proto-set-sec">
        <div className="proto-set-h">附件</div>
        <div className="proto-f-row">
          <span className="proto-f-lb">单文件大小</span>
          <input className="proto-f-inp" defaultValue="≤ 20 MB" style={{ maxWidth: 100 }} />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">允许类型</span>
          <input className="proto-f-inp mono" defaultValue=".txt .csv .json .pdf .png .jpg .pcap .har" />
        </div>
        <div className="proto-f-row">
          <span className="proto-f-lb">沙箱检测</span>
          <button className="proto-switch on" type="button" />
          <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>上传后自动查杀，可疑文件禁止智能体读取</span>
        </div>
      </div>
    </>
  )
}