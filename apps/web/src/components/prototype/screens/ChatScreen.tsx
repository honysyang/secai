// ChatScreen：工作台 = 对话流 + 轨迹 + 链路 三视图。
// 按 prototype.html 的 demo 数据静态展示。

import { useEffect, useMemo, useRef, useState } from 'react'
import type { DBShape, Task } from '../db.ts'

export interface ChatScreenProps {
  db: DBShape
  currentTask: Task | null
  onSelectTask: (id: string) => void
  onDeleteTask: (id: string) => void
  onRenameTask: (id: string, name: string) => void
  onNewTask: () => void
}

type ViewKey = 'chat' | 'trace' | 'link'
type ChatKind = 'sys' | 'user' | 'ai' | 'tool' | 'approval'

interface ToolRowData {
  name: string
  summary: string
  time: string
  status: 'ok' | 'err' | 'run'
  input: string
  output: string
}

interface ApprovalData {
  tag: string
  desc: string
  cmd: string
  args: { k: string; v: string }[]
}

interface ChatMessage {
  kind: ChatKind
  text?: string
  tool?: ToolRowData
  approval?: ApprovalData
  aiTitle?: string
  aiHtml?: string
}

const DEMO_MESSAGES: ChatMessage[] = [
  { kind: 'sys', text: '— 任务已创建 · 授权范围 demo.ine.local · 2026-09-07 21:02 —' },
  { kind: 'user', text: '对 demo.ine.local 做一次标准 Web 渗透，重点关注登录与文件上传功能，产出风险报告。' },
  {
    kind: 'ai',
    aiTitle: '侦察阶段开始',
    aiHtml: '目标为单域名 Web 应用。我将按 <span class="hl">Kill Chain</span> 顺序推进：端口与服务识别 → Web 指纹 → 目录探测 → 漏洞验证。先进行全端口扫描。',
  },
  {
    kind: 'tool',
    tool: {
      name: 'nmap',
      summary: 'Starting Nmap 7.94 · demo.ine.local · 22,80,443,8080 open',
      time: '6.2s',
      status: 'ok',
      input: 'nmap -sV -T4 --top-ports 1000 demo.ine.local',
      output: 'PORT     STATE SERVICE VERSION\n22/tcp   open  ssh     OpenSSH 8.9p1\n80/tcp   open  http    Apache httpd 2.4.57\n443/tcp  open  ssl/http Apache httpd 2.4.57\n8080/tcp open  http    Tomcat 9.0.83\nService Info: OS: Linux; CPE: cpe:/o:linux:linux_kernel',
    },
  },
  {
    kind: 'ai',
    aiTitle: '发现攻击面',
    aiHtml: '识别到 <span class="hl">3 个 Web 服务</span>，其中 <code>:8080 Tomcat 9.0.83</code> 版本较旧。目录探测发现上传接口 <span class="hl mono">/manager/html</span> 与默认凭据告警。准备验证 CVE-2024-50379（Tomcat JSP 竞争条件 RCE），该动作属于 <b>T2 中危审批卡</b>，已提交人工审批：',
  },
  {
    kind: 'approval',
    approval: {
      tag: 'T2',
      desc: '智能体请求在目标 <b class="mono">10.0.8.23:8080</b> 上执行 Tomcat 远程代码执行验证。该操作会在目标 Web 根目录写入临时探测文件并自动清理，属可逆动作。',
      cmd: 'curl -X PUT --data-binary @probe.jsp http://10.0.8.23:8080/upload/probe.jsp',
      args: [
        { k: '工具', v: 'exploit/tomcat_put_rce' },
        { k: '风险等级', v: 'T2 · 目标可写 · 动作可逆' },
        { k: '回滚机制', v: '探测完成后 5s 内自动删除 probe.jsp' },
      ],
    },
  },
  {
    kind: 'tool',
    tool: {
      name: 'gobuster',
      summary: '/admin /backup /upload /manager (200) · 218 req',
      time: '11.8s',
      status: 'ok',
      input: 'gobuster dir -u http://demo.ine.local -w common.txt',
      output: '/admin      (Status: 302)\n/backup     (Status: 200) [ext: tar.gz]\n/upload     (Status: 200)\n/manager    (Status: 401)',
    },
  },
  {
    kind: 'tool',
    tool: {
      name: 'hydra',
      summary: '对 /manager 尝试弱口令 · 已尝试 412 / 1434 组合',
      time: '…',
      status: 'run',
      input: 'hydra -L users.txt -P pass.txt 10.0.8.23 http-get /manager/html',
      output: '[8080][http-get] host: 10.0.8.23  login: tomcat  password: s3cret',
    },
  },
]

interface TraceItem {
  name: string
  pill: string
  time: string
  status: 'done' | 'run' | 'fail' | 'wait'
  desc: string
  pillWarn?: boolean
}

const DEMO_TRACE: TraceItem[] = [
  { name: 'nmap', pill: '侦察', time: '21:02:14 · 38s', status: 'done', desc: '全端口扫描 <code>demo.ine.local</code> → 开放 22 / 80 / 443 / 8080，识别 Tomcat 8.5 服务' },
  { name: 'whatweb', pill: '侦察', time: '21:02:56 · 12s', status: 'done', desc: 'Web 指纹识别 → Apache Tomcat 8.5.61 / 无 WAF / 默认错误页' },
  { name: 'dirsearch', pill: '扫描', time: '21:03:11 · 1m52s', status: 'done', desc: '目录探测 → 命中 <code>/manager</code>（HTTP Basic）、<code>/examples</code>、<code>/docs</code>' },
  { name: 'smb_enumshares', pill: '枚举', time: '21:04:08 · 9s', status: 'fail', desc: 'SMB 共享枚举失败 · 目标未开放 445 端口，智能体已标记为不适用并继续' },
  { name: 'nuclei', pill: '验证', time: '21:04:20 · 2m08s', status: 'done', desc: '漏扫验证 → 命中 <code>CVE-2017-12615</code>（Tomcat PUT 任意文件写入，CVSS 8.1）' },
  { name: 'exploit/tomcat_put_rce', pill: 'T2 审批已通过', time: '21:07:15 · 44s', status: 'done', desc: 'PUT 方式写入 <code>shell.jsp</code> 并验证代码执行 → 已获取 WebShell（证据已入证据链）', pillWarn: true },
  { name: 'hydra', pill: '凭据', time: '21:09:02 · 运行中', status: 'run', desc: '对 <code>/manager</code> 尝试弱口令 · 已尝试 412 / 1434 组合 · 预计剩余 3m10s' },
  { name: '横向移动评估', pill: '规划', time: '排队中', status: 'wait', desc: '等待凭据测试结果 · 将评估 <code>10.0.8.0/24</code> 内网可达范围（需 T3 审批）' },
]

const GROUP_ORDER: Task['group'][] = ['计划中 · 定时', '进行中', '今天', '过去七天']

export function ChatScreen({ db, currentTask, onSelectTask, onDeleteTask, onRenameTask, onNewTask }: ChatScreenProps) {
  const [view, setView] = useState<ViewKey>('chat')
  const [convOpen, setConvOpen] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1280 : true)
  const [search, setSearch] = useState('')
  const [openTools, setOpenTools] = useState<Record<number, boolean>>({ 0: true, 1: false, 2: true })
  const [approvalState, setApprovalState] = useState<'pending' | 'approved' | 'rejected'>('pending')

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase()
    return GROUP_ORDER.map((g) => ({
      g,
      list: db.tasks.filter((t) => t.group === g && (!q || t.name.toLowerCase().includes(q))),
    })).filter((x) => x.list.length > 0)
  }, [db.tasks, search])

  const target = currentTask?.target ?? '—'

  // 模拟 hydra 流式
  const [liveSummary, setLiveSummary] = useState('对 /manager 尝试弱口令 · 已尝试 412 / 1434 组合')
  useEffect(() => {
    const arr = [412, 587, 743, 902, 1104, 1231]
    let i = 0
    const t = setInterval(() => {
      i = (i + 1) % arr.length
      setLiveSummary(`对 /manager 尝试弱口令 · 已尝试 ${arr[i]} / 1434 组合`)
    }, 1800)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="proto-scr-chat on">
      {/* 会话栏 */}
      <div className="proto-sess-bar">
        <span className="proto-sess-target mono">{target}</span>
        <button className="proto-mode-chip" type="button" title="切换作战模式">
          <span className="mc-dot" style={{ background: '#378ADD' }} />
          渗透测试
        </button>
        <span className="proto-badge soft-ok">执行中</span>
        <span className="proto-spacer" />
        <span className="proto-sess-meta">
          <span>tokens <b>48.2k</b></span>
          <span>速率 <b>62 t/s</b></span>
          <span>步骤 <b>18</b></span>
        </span>
        <button
          className={'proto-conv-toggle' + (convOpen ? ' on' : '')}
          type="button"
          title="任务面板"
          aria-pressed={convOpen}
          onClick={() => setConvOpen((v) => !v)}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
            <path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" />
          </svg>
        </button>
        <div className="proto-view-tabs" role="tablist">
          <button className={'proto-view-tab' + (view === 'chat' ? ' on' : '')} type="button" onClick={() => setView('chat')}>对话</button>
          <button className={'proto-view-tab' + (view === 'trace' ? ' on' : '')} type="button" onClick={() => setView('trace')}>轨迹</button>
          <button className={'proto-view-tab' + (view === 'link' ? ' on' : '')} type="button" onClick={() => setView('link')}>链路</button>
        </div>
      </div>

      <div className="proto-chat-main">
        {convOpen && (
          <aside className="proto-conv-panel" aria-label="渗透任务目录">
            <div className="proto-sb-head">
              <span className="proto-sb-title">渗透任务</span>
              <span className="proto-badge soft-info" style={{ flex: 1, marginLeft: 2 }}>{db.tasks.length}</span>
              <button className="proto-new-btn" type="button" onClick={onNewTask}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                新任务
              </button>
            </div>
            <div className="proto-sb-search">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
              </svg>
              <input
                placeholder="搜索任务、目标…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="proto-sb-list">
              {groups.map((g) => (
                <div key={g.g}>
                  <div className="proto-sb-group">{g.g} · {g.list.length}</div>
                  {g.list.map((t) => (
                    <TaskRow
                      key={t.id}
                      task={t}
                      active={t.id === db.meta.curTaskId}
                      onSelect={() => onSelectTask(t.id)}
                      onDelete={() => onDeleteTask(t.id)}
                      onRename={(name) => onRenameTask(t.id, name)}
                    />
                  ))}
                </div>
              ))}
              {groups.length === 0 && (
                <div className="proto-sb-group">没有匹配的任务</div>
              )}
            </div>
          </aside>
        )}

        <div className="proto-chat-col">
          <div className={'proto-chat-wrap' + (view === 'chat' ? ' on' : '')}>
            <div className="proto-chat-flow">
              {DEMO_MESSAGES.map((m, i) => (
                <MessageBlock
                  key={i}
                  message={m}
                  index={i}
                  openTools={openTools}
                  setOpenTools={setOpenTools}
                  liveSummary={liveSummary}
                  approvalState={approvalState}
                  onApprove={() => setApprovalState('approved')}
                  onReject={() => setApprovalState('rejected')}
                />
              ))}
              <ChatInput />
            </div>
          </div>

          <div className={'proto-chat-wrap' + (view === 'trace' ? ' on' : '')}>
            <div className="proto-trace-wrap">
              <div className="proto-trace-stats">
                <span className="proto-badge">18 步</span>
                <span className="proto-badge soft-ok">成功 16</span>
                <span className="proto-badge soft-err">失败 1</span>
                <span className="proto-badge soft-run">运行中 1</span>
                <span className="proto-badge">累计 6m42s</span>
              </div>
              {DEMO_TRACE.map((t, i) => (
                <div key={i} className={'proto-tn ' + t.status}>
                  <div className="proto-tn-rail"><span className="proto-tn-dot" /></div>
                  <div className="proto-tn-body">
                    <div className="proto-tn-head">
                      <span className="proto-tn-name">{t.name}</span>
                      <span className={'proto-pill' + (t.pillWarn ? ' warn' : '')}>{t.pill}</span>
                      <span className="proto-tn-time">{t.time}</span>
                    </div>
                    <div className="proto-tn-desc" dangerouslySetInnerHTML={{ __html: t.desc }} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className={'proto-chat-wrap' + (view === 'link' ? ' on' : '')}>
            <div className="proto-link-wrap">
              <div className="proto-link-legend">
                <span><i style={{ background: 'var(--brand-500)' }} />意图</span>
                <span><i style={{ background: 'var(--text-3)' }} />资产</span>
                <span><i style={{ background: 'var(--warn)' }} />事实</span>
                <span><i style={{ background: 'var(--crit)' }} />漏洞</span>
                <span><i style={{ background: 'transparent', borderTop: '2px solid var(--brand-500)', height: 0, width: 14 }} />已证实路径</span>
                <span><i style={{ background: 'transparent', borderTop: '2px dashed var(--ok)', height: 0, width: 14 }} />进行中</span>
              </div>
              <LinkSVG />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function MessageBlock({
  message,
  index,
  openTools,
  setOpenTools,
  liveSummary,
  approvalState,
  onApprove,
  onReject,
}: {
  message: ChatMessage
  index: number
  openTools: Record<number, boolean>
  setOpenTools: React.Dispatch<React.SetStateAction<Record<number, boolean>>>
  liveSummary: string
  approvalState: 'pending' | 'approved' | 'rejected'
  onApprove: () => void
  onReject: () => void
}) {
  if (message.kind === 'sys') {
    return <div className="proto-sys-line">{message.text}</div>
  }
  if (message.kind === 'user') {
    return <div className="proto-msg-user">{message.text}</div>
  }
  if (message.kind === 'ai') {
    return (
      <div className="proto-msg-ai">
        <h4>{message.aiTitle}</h4>
        <p dangerouslySetInnerHTML={{ __html: message.aiHtml ?? '' }} />
      </div>
    )
  }
  if (message.kind === 'tool' && message.tool) {
    const open = openTools[index] ?? false
    const isLive = message.tool.name === 'hydra'
    return (
      <div className={'proto-tool-row' + (open ? ' open' : '')}>
        {isLive && <div className="proto-running-bar" />}
        <div
          className="proto-tool-head"
          onClick={() => setOpenTools((s) => ({ ...s, [index]: !open }))}
        >
          <span className={'proto-tool-dot ' + message.tool.status} />
          <span className="proto-tool-name">{message.tool.name}</span>
          <span className="proto-tool-summ">
            {isLive ? liveSummary : message.tool.summary}
          </span>
          <span className="proto-tool-time">{message.tool.time}</span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--text-3)' }}>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </div>
        <div className="proto-tool-body">
          <div className="proto-io-grid">
            <div className="proto-io-cell">
              <div className="proto-io-label">INPUT</div>
              <div className="proto-io-code">{message.tool.input}</div>
            </div>
            <div className="proto-io-cell">
              <div className="proto-io-label">OUTPUT</div>
              <div className="proto-io-code">{message.tool.output}</div>
            </div>
          </div>
        </div>
      </div>
    )
  }
  if (message.kind === 'approval' && message.approval) {
    const a = message.approval
    if (approvalState !== 'pending') {
      const color = approvalState === 'approved' ? 'var(--ok)' : 'var(--crit)'
      const text = approvalState === 'approved'
        ? '已批准 · 智能体将继续执行（写入审计日志 + 操作人 + 时间戳）'
        : '已拒绝 · 智能体将跳过该动作并调整策略'
      return (
        <div className="proto-approval" style={{ borderColor: color, borderLeftWidth: 4, background: 'var(--surface)' }}>
          <div className="proto-appr-resolved" style={{ color }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d={approvalState === 'approved' ? 'M20 6 9 17l-5-5' : 'M18 6 6 18M6 6l12 12'} />
            </svg>
            {text}
          </div>
        </div>
      )
    }
    return (
      <div className="proto-approval">
        <div className="proto-appr-head">
          <span className="proto-appr-tag">{a.tag}</span>
          等待人工审批
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>#0241 · 已等待 00:42</span>
        </div>
        <div className="proto-appr-body">
          <div className="proto-appr-desc" dangerouslySetInnerHTML={{ __html: a.desc }} />
          <div className="proto-appr-cmd">{a.cmd}</div>
          <div className="proto-appr-args">
            {a.args.map((arg, i) => (
              <div key={i} className="arg"><span className="k">{arg.k}</span><span className="v">{arg.v}</span></div>
            ))}
          </div>
          <div className="proto-appr-foot">
            <input className="proto-appr-note" placeholder="审批备注（可选，将写入审计日志）" />
            <button className="proto-btn ghost" onClick={onReject}>拒绝</button>
            <button className="proto-btn primary" onClick={onApprove}>允许执行</button>
          </div>
        </div>
        <div className="proto-appr-policy">
          <label><input type="checkbox" /> 允许后自动放行同类动作（写入审批策略）</label>
          <span className="proto-appr-link">配置规则 →</span>
        </div>
      </div>
    )
  }
  return null
}

function LinkSVG() {
  const nodes = [
    { x: 15, y: 70, t: '获取 Web 入口', s: '突破口 · 优先级高' },
    { x: 15, y: 180, t: '获取有效凭据', s: '后台 / 服务口令' },
    { x: 15, y: 270, t: '扩大内网控制', s: '横向 · 待评估' },
    { x: 255, y: 70, t: 'demo.ine.local', s: '主域名 · Web 应用', mono: true },
    { x: 255, y: 180, t: '10.0.8.23:8080', s: 'Tomcat 9.0.83', mono: true },
    { x: 255, y: 270, t: '10.0.8.15', s: '内网存活主机 · 新发现', mono: true },
    { x: 495, y: 70, t: 'PUT 方法开启', s: '任意文件写入可能', warn: true },
    { x: 495, y: 180, t: '弱口令命中', s: 'tomcat : s3cret', mono: true, warn: true },
    { x: 495, y: 270, t: 'SMB 可达', s: '探测中 · 待证实', mono: true, ok: true },
    { x: 755, y: 70, t: 'CVE-2017-12615', s: 'CVSS 8.1 · critical', mono: true, crit: true },
    { x: 755, y: 180, t: '后台弱口令', s: 'CVSS 7.5 · high', high: true },
    { x: 755, y: 270, t: '横向移动风险', s: '进行中 · 待证实', ok: true },
  ]
  return (
    <svg className="proto-link-svg" viewBox="0 0 920 400" role="img" aria-label="渗透链路图">
      <defs>
        <marker id="ln-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </marker>
      </defs>
      <text x="90" y="24" textAnchor="middle" fill="var(--text-3)" fontSize="10.5" fontWeight="700" style={{ letterSpacing: '.08em' }}>意图</text>
      <text x="330" y="24" textAnchor="middle" fill="var(--text-3)" fontSize="10.5" fontWeight="700" style={{ letterSpacing: '.08em' }}>资产</text>
      <text x="570" y="24" textAnchor="middle" fill="var(--text-3)" fontSize="10.5" fontWeight="700" style={{ letterSpacing: '.08em' }}>事实</text>
      <text x="830" y="24" textAnchor="middle" fill="var(--text-3)" fontSize="10.5" fontWeight="700" style={{ letterSpacing: '.08em' }}>漏洞</text>
      {[
        'M165 92 Q210 92 255 92',
        'M165 202 Q210 202 255 202',
        'M165 292 Q210 292 255 292',
        'M405 92 Q450 92 495 92',
        'M405 202 Q450 202 495 202',
        'M405 292 Q450 292 495 292',
        'M645 92 Q700 92 755 92',
        'M645 202 Q700 202 755 202',
        'M645 292 Q700 292 755 292',
      ].map((d, i) => {
        const run = i === 5 || i === 8
        return (
          <path
            key={i}
            d={d}
            fill="none"
            stroke={run ? 'var(--ok)' : 'var(--brand-500)'}
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeDasharray={run ? '5 4' : undefined}
            markerEnd="url(#ln-arrow)"
          />
        )
      })}
      {nodes.map((n, i) => {
        const fill = n.crit ? 'rgba(239,68,68,.07)' : n.high ? 'rgba(245,166,35,.07)' : n.ok ? 'rgba(48,164,108,.07)' : 'var(--surface-2)'
        const stroke = n.warn ? 'var(--warn)' : n.crit ? 'var(--crit)' : n.high ? 'var(--warn)' : n.ok ? 'var(--ok)' : 'var(--border-2)'
        const strokeOp = n.warn || n.ok ? 0.5 : 1
        return (
          <g key={i}>
            <rect x={n.x} y={n.y} width={150} height={44} rx={8}
              fill={fill}
              stroke={stroke}
              strokeOpacity={strokeOp}
              strokeWidth="1" />
            <text x={n.x + 75} y={n.y + 18} textAnchor="middle"
              fontSize="11.5" fontWeight="600" fill="var(--text-1)">{n.t}</text>
            <text x={n.x + 75} y={n.y + 33} textAnchor="middle"
              fontSize="10" fill="var(--text-3)" fontFamily={n.mono ? 'var(--mono)' : 'inherit'}>
              {n.s}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function ChatInput() {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<Array<{ name: string; size: number }>>([])
  const [drag, setDrag] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const pillRef = useRef<HTMLDivElement>(null)

  const onSend = () => {
    if (!text.trim() && files.length === 0) return
    setText('')
    setFiles([])
  }

  return (
    <div className="proto-input-dock">
      <div
        ref={pillRef}
        className={'proto-input-pill' + (drag ? ' drag' : '')}
        onDragEnter={(e) => { e.preventDefault(); setDrag(true) }}
        onDragOver={(e) => { e.preventDefault() }}
        onDragLeave={(e) => { if (!pillRef.current?.contains(e.relatedTarget as Node)) setDrag(false) }}
        onDrop={(e) => {
          e.preventDefault()
          setDrag(false)
          const list = Array.from(e.dataTransfer?.files ?? [])
          setFiles((cur) => [...cur, ...list.map((f) => ({ name: f.name, size: f.size }))])
        }}
      >
        <div className="proto-drop-hint">松开即可添加为附件</div>
        {files.length > 0 && (
          <div className="proto-att-chips">
            {files.map((f, i) => (
              <span key={i} className="proto-att-chip">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" />
                </svg>
                <span className="ac-n">{f.name}</span>
                <span className="ac-s">{fmtBytes(f.size)}</span>
                <button type="button" onClick={() => setFiles((arr) => arr.filter((_, j) => j !== i))}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          rows={1}
          placeholder="向破阵下达指令，如「按附件中的目标清单逐项验证」…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
          onPaste={(e) => {
            const items = Array.from(e.clipboardData?.items ?? [])
            const fs: File[] = []
            items.forEach((it) => {
              if (it.kind === 'file') {
                const f = it.getAsFile()
                if (f) fs.push(f)
              }
            })
            if (fs.length > 0) {
              e.preventDefault()
              setFiles((cur) => [...cur, ...fs.map((f) => ({ name: f.name, size: f.size }))])
            }
          }}
        />
        <div className="proto-ip-row">
          <button type="button" className="proto-ip-plus" title="新建任务">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <button type="button" className="proto-ip-plus" title="添加附件" onClick={() => fileRef.current?.click()}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.4 11.05 12.3 20.2a5.5 5.5 0 0 1-7.8-7.8l9.2-9.2a3.7 3.7 0 0 1 5.2 5.2l-9.2 9.2a1.8 1.8 0 0 1-2.6-2.6l8.4-8.4" />
            </svg>
          </button>
          <span className="proto-ip-hint"><kbd>Enter</kbd> 发送 · <kbd>Shift+Enter</kbd> 换行 · 可拖拽或粘贴文件</span>
          <button type="button" className="proto-send-btn" onClick={onSend}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m22 2-7 20-4-9-9-4z" /><path d="M22 2 11 13" />
            </svg>
          </button>
        </div>
      </div>
      <input
        ref={fileRef}
        type="file"
        hidden
        multiple
        accept=".txt,.csv,.json,.pdf,.png,.jpg,.jpeg,.pcap,.har"
        onChange={(e) => {
          const arr = Array.from(e.target.files ?? [])
          setFiles((cur) => [...cur, ...arr.map((f) => ({ name: f.name, size: f.size }))])
          e.target.value = ''
        }}
      />
    </div>
  )
}

function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1048576).toFixed(1)} MB`
}

function TaskRow({ task, active, onSelect, onDelete, onRename }: {
  task: Task
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (name: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(task.name)
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div
      className={'proto-sb-item' + (active ? ' on' : '')}
      data-id={task.id}
      onClick={() => { if (!editing) onSelect() }}
    >
      <span className={'proto-dot ' + task.status} />
      {editing ? (
        <input
          className="proto-sb-rename-input"
          value={value}
          autoFocus
          onChange={(e) => setValue(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={() => { if (value.trim() && value !== task.name) onRename(value.trim()); setEditing(false) }}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') {
              if (value.trim()) onRename(value.trim())
              setEditing(false)
            } else if (e.key === 'Escape') {
              setValue(task.name)
              setEditing(false)
            }
          }}
        />
      ) : (
        <span className="proto-sb-name">{task.name}</span>
      )}
      {task.next && <span className="proto-sched-next mono">{task.next}</span>}
      <button
        type="button"
        className="proto-sb-kebab"
        aria-label="任务操作"
        onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v) }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="5" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="12" cy="19" r="1.7" />
        </svg>
      </button>
      {menuOpen && (
        <div className="proto-sb-menu" role="menu">
          <button
            type="button"
            className="proto-sb-menu-item"
            onClick={(e) => { e.stopPropagation(); setMenuOpen(false); setEditing(true) }}
          >
            重命名
          </button>
          <button
            type="button"
            className="proto-sb-menu-item"
            onClick={(e) => { e.stopPropagation(); setMenuOpen(false) }}
          >
            设置定时
          </button>
          <button
            type="button"
            className="proto-sb-menu-item danger"
            onClick={(e) => { e.stopPropagation(); setMenuOpen(false); onDelete() }}
          >
            删除任务
          </button>
        </div>
      )}
    </div>
  )
}