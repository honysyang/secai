// ChatScreen：工作台 = 对话流 + 轨迹 + 链路 三视图。
// 按 prototype.html 的 demo 数据静态展示。

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { DBShape, Task } from '../db.ts'
import type { CombatMode } from '../ModeModal.tsx'
import { COMBAT_MODE_MAP, ModeBanner } from '../ModeModal.tsx'
import type { ProtoRuntime, ProtoChatMessage } from '../ProtoRuntime.ts'
import { deriveMessages, deriveApprovalResolutions } from '../ProtoRuntime.ts'
import type { SessionSnapshot } from '../../../runtime/session.ts'

/** 任务行展开态：目标会话子项（WS 实时态，经当前活动集群投影）。 */
interface TaskSessionRow {
  sessionId: string
  target: string
  status: SessionSnapshot['status']
}

export interface ChatScreenProps {
  db: DBShape
  proto: ProtoRuntime
  currentTask: Task | null
  currentMode: CombatMode
  onSelectTask: (id: string) => void
  onDeleteTask: (id: string) => void
  onRenameTask: (id: string, name: string) => void
  onNewTask: () => void
  onOpenSched: (taskId: string) => void
  onOpenMode: () => void
  onOpenSettings: () => void
}

type ViewKey = 'chat' | 'trace' | 'link'

/** 从 ProtoRuntime 投影对话消息（安全包装：异常时回退空数组，不拖垮渲染）。 */
function deriveMessagesSafe(session: SessionSnapshot | null): ProtoChatMessage[] {
  try {
    return session === null ? [] : deriveMessages(session)
  } catch {
    return []
  }
}

function deriveResolutionsSafe(session: SessionSnapshot | null): Record<string, 'allow' | 'deny'> {
  try {
    return session === null ? {} : deriveApprovalResolutions(session)
  } catch {
    return {}
  }
}

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

export function ChatScreen(props: ChatScreenProps) {
  const { db, proto, currentTask, currentMode, onSelectTask, onDeleteTask, onRenameTask, onNewTask, onOpenMode, onOpenSettings } = props
  const onOpenSched = props.onOpenSched
  const [view, setView] = useState<ViewKey>('chat')
  const [convOpen, setConvOpen] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1280 : true)
  const [search, setSearch] = useState('')
  const [openTools, setOpenTools] = useState<Record<string, boolean>>({})

  // 选中会话实时快照（WS 驱动）→ 对话流消息
  const session = useSyncExternalStore(proto.subscribeSession, proto.currentSession, proto.currentSession)
  const messages = useMemo<ProtoChatMessage[]>(() => deriveMessagesSafe(session), [session])
  const approvalResolutions = useMemo(() => (session === null ? {} : deriveResolutionsSafe(session)), [session])
  const flowRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = flowRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length])

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase()
    return GROUP_ORDER.map((g) => ({
      g,
      list: db.tasks.filter((t) => t.group === g && (!q || t.name.toLowerCase().includes(q))),
    })).filter((x) => x.list.length > 0)
  }, [db.tasks, search])

  const target = currentTask?.target ?? session?.header?.target ?? '—'
  const linkState = proto.link()

  return (
    <div className="proto-scr-chat on">
      {/* 会话栏 */}
      <div className="proto-sess-bar">
        <span className="proto-sess-target mono">{target}</span>
        <button className="proto-mode-chip" type="button" title="切换作战模式" onClick={onOpenMode}>
          <span className="mc-dot" style={{ background: COMBAT_MODE_MAP[currentMode].c }} />
          {COMBAT_MODE_MAP[currentMode].n}
        </button>
        <span className={'proto-badge ' + (linkState === 'connected' ? 'soft-ok' : 'soft-run')}>
          {linkState === 'connected' ? '已连接' : linkState === 'connecting' ? '连接中' : '重连中'}
        </span>
        <span className="proto-spacer" />
        <span className="proto-sess-meta">
          <span>tokens <b>{session === null ? '0' : String(session.events.length * 1000)}</b></span>
          <span>步骤 <b>{session === null ? '0' : String(session.events.length)}</b></span>
          <span>审批 <b>{session === null ? '0' : String(session.pendingApprovals.length)}</b></span>
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

      <ModeBanner
        mode={currentMode}
        onOpenSettings={onOpenSettings}
        onSwitchMode={onOpenMode}
        onApply={() => { /* 模型应用反馈由 ProtoLayout 处理  */ }}
      />

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
                    proto={proto}
                    active={t.id === db.meta.curTaskId}
                    onSelect={() => onSelectTask(t.id)}
                    onDelete={() => onDeleteTask(t.id)}
                    onRename={(name) => onRenameTask(t.id, name)}
                    onOpenSched={() => onOpenSched(t.id)}
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
            <div className="proto-chat-flow" ref={flowRef}>
              {messages.length === 0 && (
                <div className="proto-sys-line">
                  {session === null
                    ? '— 暂无选中会话 · 从左侧任务目录选择或新建任务 —'
                    : '— 等待智能体事件流（尚无消息） —'}
                </div>
              )}
              {messages.map((m) => (
                <MessageBlock
                  key={m.key}
                  message={m}
                  openTools={openTools}
                  setOpenTools={setOpenTools}
                  resolutions={approvalResolutions}
                  onApprove={(rpcId, comment) => proto.respond(rpcId, 'allow', comment)}
                  onReject={(rpcId, comment) => proto.respond(rpcId, 'deny', comment)}
                />
              ))}
              <ChatInput onSend={(text) => proto.send(text)} />
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
  openTools,
  setOpenTools,
  resolutions,
  onApprove,
  onReject,
}: {
  message: ProtoChatMessage
  openTools: Record<string, boolean>
  setOpenTools: React.Dispatch<React.SetStateAction<Record<string, boolean>>>
  resolutions: Record<string, 'allow' | 'deny'>
  onApprove: (rpcId: string, comment?: string) => void
  onReject: (rpcId: string, comment?: string) => void
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
    const open = openTools[message.key] ?? false
    const isLive = message.tool.status === 'run'
    return (
      <div className={'proto-tool-row' + (open ? ' open' : '')}>
        {isLive && <div className="proto-running-bar" />}
        <div
          className="proto-tool-head"
          onClick={() => setOpenTools((s) => ({ ...s, [message.key]: !open }))}
        >
          <span className={'proto-tool-dot ' + message.tool.status} />
          <span className="proto-tool-name">{message.tool.name}</span>
          <span className="proto-tool-summ">{message.tool.summary}</span>
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
  if (message.kind === 'approval') {
    // 已决审批（resolved 标记）或带 approval 数据且已裁决 → 决议面板
    const resolved = message.resolved ?? (message.approval ? resolutions[message.approval.rpcId] : undefined)
    if (resolved !== undefined) {
      const color = resolved === 'allow' ? 'var(--ok)' : 'var(--crit)'
      const text = resolved === 'allow'
        ? '已批准 · 智能体将继续执行（写入审计日志 + 操作人 + 时间戳）'
        : '已拒绝 · 智能体将跳过该动作并调整策略'
      return (
        <div className="proto-approval" style={{ borderColor: color, borderLeftWidth: 4, background: 'var(--surface)' }}>
          <div className="proto-appr-resolved" style={{ color }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d={resolved === 'allow' ? 'M20 6 9 17l-5-5' : 'M18 6 6 18M6 6l12 12'} />
            </svg>
            {text}
          </div>
        </div>
      )
    }
    if (!message.approval) return null
    const a = message.approval
    return (
      <div className="proto-approval">
        <div className="proto-appr-head">
          <span className="proto-appr-tag">{a.tag}</span>
          等待人工审批
          <span style={{ flex: 1 }} />
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>{a.rpcId}</span>
        </div>
        <div className="proto-appr-body">
          <div className="proto-appr-desc" dangerouslySetInnerHTML={{ __html: a.desc }} />
          {a.cmd && <div className="proto-appr-cmd">{a.cmd}</div>}
          {a.args.length > 0 && (
            <div className="proto-appr-args">
              {a.args.map((arg, i) => (
                <div key={i} className="arg"><span className="k">{arg.k}</span><span className="v">{arg.v}</span></div>
              ))}
            </div>
          )}
          <div className="proto-appr-foot">
            <input className="proto-appr-note" placeholder="审批备注（可选，将写入审计日志）" />
            <button className="proto-btn ghost" onClick={(e) => {
              const note = (e.currentTarget.parentElement?.querySelector('.proto-appr-note') as HTMLInputElement | null)?.value
              onReject(a.rpcId, note || undefined)
            }}>拒绝</button>
            <button className="proto-btn primary" onClick={(e) => {
              const note = (e.currentTarget.parentElement?.querySelector('.proto-appr-note') as HTMLInputElement | null)?.value
              onApprove(a.rpcId, note || undefined)
            }}>允许执行</button>
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

function ChatInput({ onSend }: { onSend: (text: string) => void }) {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<Array<{ name: string; size: number }>>([])
  const [drag, setDrag] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const pillRef = useRef<HTMLDivElement>(null)

  const handleSend = () => {
    if (!text.trim()) return
    onSend(text.trim())
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
              handleSend()
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
          <button type="button" className="proto-send-btn" onClick={handleSend}>
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

function TaskRow({ task, proto, active, onSelect, onDelete, onRename, onOpenSched }: {
  task: Task
  proto: ProtoRuntime
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (name: string) => void
  onOpenSched: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(task.name)
  const [menuOpen, setMenuOpen] = useState(false)
  // 目标会话子项：任务行激活时展开（WS 实时态，随任务行重渲染刷新）
  const sessRows: ReadonlyArray<TaskSessionRow> = active ? proto.clusterSessions(task.id) : []

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
            onClick={(e) => { e.stopPropagation(); setMenuOpen(false); onOpenSched() }}
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
      {sessRows.length > 0 && (
        <div className="proto-sb-sess" onClick={(e) => e.stopPropagation()}>
          {sessRows.map((s) => (
            <button
              key={s.sessionId}
              type="button"
              className="proto-sb-sess-item"
              title={s.target}
              onClick={() => proto.selectSession(s.sessionId)}
            >
              <span className={'proto-dot ' + (s.status === 'running' ? 'run' : s.status === 'awaiting_approval' ? 'appr' : s.status === 'completed' ? 'done' : s.status === 'failed' ? 'fail' : 'stop')} />
              <span className="proto-sb-sess-name">{s.target}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}