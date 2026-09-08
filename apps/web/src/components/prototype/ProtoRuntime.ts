// ProtoRuntime：把 AppRuntime 的 WS 快照映射为 ProtoLayout 的本地数据面（DBShape）。
//
// 桥接职责：
// 1. 任务目录：TaskSummary → Task（id=engagementId；分组/状态/调度元数据经 localStorage 本地覆盖层
//    secai-pt-meta-v1 持久化——作战模式、调度、分组偏好是前端展示层概念，后端 engagement 无此语义）
// 2. 会话状态：会话运行状态映射到行 status（running→run 等），本地 stop 覆盖优先（软停）
// 3. 对话流：选中会话的 SessionSnapshot.events → ChatMessage[]（映射见 eventToMessage）
// 4. 动作代理：增删改查/指令/审批全部走真实 RPC（AppRuntime）
//
// 不改动 AppRuntime 物理订阅协议，经新增的 runtime.pick 响应式切片读取。

import type { AppRuntime } from '../../runtime/appRuntime.ts'
import type { AppSnapshot } from '../../runtime/appRuntime.ts'
import type { SessionSnapshot } from '../../runtime/session.ts'
import type { SessionEvent, ApprovalRequest } from '../../connection/api.ts'
import { DEFAULT_DB } from './db.ts'
import type { DBShape, Task, TaskStatus } from './db.ts'

/** 导出 Task 的展示元数据键集合（供外部覆写）。 */
export type { Task, TaskStatus, DBShape }

// ───────────────────────── 本地元数据覆盖层（localStorage） ─────────────────────────

const META_KEY = 'secai-pt-meta-v1'

/** 任务本地展示层元数据（id=engagementId）：模式/调度/分组偏好 + 软停标记。 */
export interface TaskMetaEntry {
  mode?: Task['mode']
  sched?: Task['sched']
  next?: string
  group?: Task['group']
  stopped?: boolean
}

export interface ProtoMeta {
  /** engagementId → 本地元数据。 */
  tasks: Record<string, TaskMetaEntry>
  curTaskId: string
  curMode: Task['mode']
}

const DEFAULT_META: ProtoMeta = { tasks: {}, curTaskId: '', curMode: 'pt' }

export function loadMeta(): ProtoMeta {
  if (typeof localStorage === 'undefined') return DEFAULT_META
  try {
    const raw = localStorage.getItem(META_KEY)
    if (!raw) return DEFAULT_META
    const parsed = JSON.parse(raw) as Partial<ProtoMeta>
    return {
      tasks: parsed.tasks ?? {},
      curTaskId: parsed.curTaskId ?? '',
      curMode: parsed.curMode ?? 'pt',
    }
  } catch {
    return DEFAULT_META
  }
}

export function saveMeta(meta: ProtoMeta): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(META_KEY, JSON.stringify(meta))
  } catch {
    // quota / unavailable：吞掉
  }
}

// ───────────────────────── 状态映射 ─────────────────────────

/** 会话状态 → 行圆点状态。 */
function sessionToTaskStatus(s: SessionSnapshot, meta: TaskMetaEntry | undefined): TaskStatus {
  if (meta?.stopped === true) return 'stop'
  switch (s.status) {
    case 'running':
      return 'run'
    case 'awaiting_approval':
      return 'appr'
    case 'completed':
      return 'done'
    case 'failed':
      return 'fail'
    case 'idle':
    case 'stopped':
      return 'stop'
    default:
      return 'stop'
  }
}

/** engagement 状态（无会话行头时兜底）→ 行圆点状态。 */
function engagementToTaskStatus(status: 'running' | 'completed' | 'failed'): TaskStatus {
  if (status === 'running') return 'run'
  if (status === 'completed') return 'done'
  return 'fail'
}

/** 按天分组：今天 / 过去七天 / 更早（计划中·定时由调度元数据优先）。 */
function groupByDay(updatedAt: string): Task['group'] {
  const ts = Date.parse(updatedAt)
  if (Number.isNaN(ts)) return '过去七天'
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const dayMs = 24 * 60 * 60 * 1000
  if (ts >= startOfToday) return '今天'
  if (ts >= startOfToday - 6 * dayMs) return '过去七天'
  return '过去七天'
}

/** 由 WS 快照 + 本地元数据合成 ProtoLayout 的 DBShape（单一数据源）。 */
export function deriveDB(snapshot: AppSnapshot, meta: ProtoMeta): DBShape {
  const tasks: Task[] = snapshot.tasks.map((t) => {
    const m = meta.tasks[t.engagementId]
    // 活动集群首个会话的运行状态（running/appr 等实时态优先于 engagement 行头）
    let liveStatus: TaskStatus | null = null
    let target = ''
    if (snapshot.activeTaskId === t.engagementId && snapshot.engagement.sessions.length > 0) {
      const s = snapshot.engagement.sessions[0]!
      target = s.header?.target ?? ''
      liveStatus = sessionToTaskStatus(s, m)
    }
    const status = liveStatus ?? engagementToTaskStatus(t.status)
    const sched = m?.sched ?? 'now'
    const group: Task['group'] =
      sched !== 'now'
        ? '计划中 · 定时'
        : (m?.group ?? groupByDay(t.updatedAt))
    return {
      id: t.engagementId,
      name: t.title,
      target: target || (snapshot.engagement.header?.title ?? t.title),
      status,
      group,
      mode: m?.mode ?? 'pt',
      sched,
      next: m?.next ?? '',
    }
  })
  return {
    tasks,
    settings: DEFAULT_DB.settings,
    members: DEFAULT_DB.members,
    apis: DEFAULT_DB.apis,
    rules: DEFAULT_DB.rules,
    meta: { curTaskId: meta.curTaskId, curMode: meta.curMode },
  }
}

// ───────────────────────── 对话流映射 ─────────────────────────

export interface ProtoToolRow {
  name: string
  summary: string
  time: string
  status: 'ok' | 'err' | 'run'
  input: string
  output: string
}

export interface ProtoApproval {
  rpcId: string
  tag: string
  desc: string
  cmd: string
  args: { k: string; v: string }[]
}

export type ProtoChatKind = 'sys' | 'user' | 'ai' | 'tool' | 'approval'

export interface ProtoChatMessage {
  kind: ProtoChatKind
  key: string
  text?: string
  tool?: ProtoToolRow
  approval?: ProtoApproval
  aiTitle?: string
  aiHtml?: string
  /** 审批本条目的实时决议状态（来自 approvalLog）。 */
  resolved?: 'allow' | 'deny'
}

/** 会话事件 → 对话消息。对齐后端真实事件口径（见 runtime/eventTurns.ts）：
 * message{role,content} / tool/call{tool,args} / tool/output{tool,output,ok} / subagent / 其余 → 系统行。 */
export function eventToMessage(ev: SessionEvent): ProtoChatMessage | null {
  const d = (ev.data ?? {}) as Record<string, unknown>
  const base = { key: ev.eventId }
  const str = (v: unknown, fb = ''): string => (typeof v === 'string' ? v : fb)
  switch (ev.type) {
    case 'message': {
      // 后端口径：{ role: 'user' | 'assistant', content }
      if (str(d.role) === 'user') {
        return { ...base, kind: 'user', text: str(d.content) }
      }
      return { ...base, kind: 'ai', aiTitle: '分析', aiHtml: escapeHtml(str(d.content)) }
    }
    case 'user':
    case 'user_message': {
      const text = str(d.text, str(d.message))
      return { ...base, kind: 'user', text }
    }
    case 'assistant':
    case 'ai':
    case 'agent':
    case 'agent_message': {
      const text = str(d.text, str(d.message))
      const title = str(d.title, str(d.phase, '分析'))
      return { ...base, kind: 'ai', aiTitle: title, aiHtml: escapeHtml(text) }
    }
    case 'tool':
    case 'tool_call':
    case 'tool/call': {
      const name = str(d.name, str(d.tool, 'tool'))
      const args = d.args !== undefined ? (typeof d.args === 'string' ? d.args : JSON.stringify(d.args)) : ''
      const status = ev.type === 'tool/call' ? 'run' : d.status === 'failed' || d.status === 'error' ? 'err' : d.status === 'running' ? 'run' : 'ok'
      return {
        ...base,
        kind: 'tool',
        tool: {
          name,
          summary: str(d.summary),
          time: str(d.duration, str(d.time)),
          status,
          input: str(d.input, str(d.command, args)),
          output: str(d.output, str(d.result)),
        },
      }
    }
    case 'tool_result':
    case 'tool/output': {
      const name = str(d.name, str(d.tool, 'tool'))
      const status = d.ok === false || d.status === 'failed' || d.status === 'error' ? 'err' : 'ok'
      return {
        ...base,
        kind: 'tool',
        tool: {
          name,
          summary: str(d.summary),
          time: str(d.durationMs !== undefined ? `${d.durationMs}ms` : '', str(d.time)),
          status,
          input: str(d.input, str(d.command)),
          output: str(d.output, str(d.result)),
        },
      }
    }
    case 'subagent': {
      const state = str(d.state)
      const text = `子 agent「${str(d.name, '?')}」${state === '' ? '' : `：${state}`}`
      return { ...base, kind: 'sys', text }
    }
    case 'link': {
      // 链路事件：暂以系统行透出（完整图谱走旧视图 LinkGraph）
      const label = str(d.label, 'link')
      return { ...base, kind: 'sys', text: `链路 · ${escapeHtml(label)}` }
    }
    case 'system': {
      const text = str(d.text, JSON.stringify(d))
      return { ...base, kind: 'sys', text }
    }
    default: {
      const label = str(d.label, ev.type)
      return { ...base, kind: 'sys', text: label }
    }
  }
}

/** 挂起审批 → 审批卡消息（等待人工 respond）。 */
export function approvalToMessage(rpcId: string, payload: ApprovalRequest): ProtoChatMessage {
  const detail = payload.detail ?? {}
  const args: { k: string; v: string }[] = []
  for (const [k, v] of Object.entries(detail)) {
    args.push({ k, v: typeof v === 'string' ? v : JSON.stringify(v) })
  }
  return {
    kind: 'approval',
    key: `appr-${rpcId}`,
    approval: {
      rpcId,
      tag: 'T2',
      desc: escapeHtml(payload.description ?? payload.action ?? '请求执行敏感动作'),
      cmd: typeof detail.command === 'string' ? detail.command : payload.action,
      args,
    },
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** 把选中会话快照投影为对话消息列表（事件窗口 + 挂起审批 + 决议标记）。 */
export function deriveMessages(session: SessionSnapshot | null): ProtoChatMessage[] {
  if (session === null) return []
  const out: ProtoChatMessage[] = []
  for (const ev of session.events) {
    const m = eventToMessage(ev)
    if (m !== null) out.push(m)
  }
  // 审批条目：挂起 → 待审批卡；已决（approvalLog）→ 决议标记卡（保留在流中）
  const resolvedMap = new Map<string, 'allow' | 'deny'>()
  for (const entry of session.approvalLog) {
    resolvedMap.set(entry.rpcId, entry.resolution.decision)
  }
  const pendingIds = new Set<string>()
  for (const pa of session.pendingApprovals) {
    pendingIds.add(pa.rpcId)
    out.push(approvalToMessage(pa.rpcId, pa.payload))
  }
  for (const entry of session.approvalLog) {
    if (pendingIds.has(entry.rpcId)) continue
    out.push({
      kind: 'approval',
      key: `appr-${entry.rpcId}`,
      resolved: entry.resolution.decision,
    })
  }
  return out
}

/** 审批决议状态表（rpcId → allow/deny），用于渲染已批准/已拒绝解析面板。 */
export function deriveApprovalResolutions(session: SessionSnapshot | null): Record<string, 'allow' | 'deny'> {
  if (session === null) return {}
  const map: Record<string, 'allow' | 'deny'> = {}
  for (const entry of session.approvalLog) {
    map[entry.rpcId] = entry.resolution.decision
  }
  return map
}

// ───────────────────────── ProtoRuntime ─────────────────────────

/**
 * ProtoLayout 的数据源 + 动作层：包装 AppRuntime，对外暴露 DBShape 与真实 RPC 动作。
 * UI 只与本对象交互，不再直接读写 localStorage 任务库。
 */
export class ProtoRuntime {
  private readonly runtime: AppRuntime
  private meta: ProtoMeta
  /** DBShape 引用缓存：subscribe 投递与 getSnapshot 返回同一对象，稳定 useSyncExternalStore。
   *  以 AppRuntime 快照版本号为缓存键——同版本内快照物理不变。 */
  private dbCache: DBShape | null = null
  private dbCacheVersion = -1
  private sessionCache: SessionSnapshot | null = null
  private sessionCacheVersion = -1

  constructor(runtime: AppRuntime) {
    this.runtime = runtime
    this.meta = loadMeta()
  }

  /** 数据面活性兜底：底层 AppRuntime 幂等 start（双 WS + bootstrap）。 */
  ensureRunning = (): void => {
    this.runtime.start()
  }

  /** 订阅 ProtoLayout 数据面（DBShape）：只在快照版本变化时重建并投递。 */
  subscribe = (listener: (db: DBShape) => void): (() => void) => {
    let lastVersion = -1
    return this.runtime.subscribe((snapshot) => {
      const v = this.runtime.version
      if (v === lastVersion && this.dbCache !== null) {
        listener(this.dbCache)
        return
      }
      lastVersion = v
      const db = this.buildDB(snapshot)
      this.dbCache = db
      this.dbCacheVersion = v
      listener(db)
    })
  }

  /** 当前 DBShape（getSnapshot 语义；同版本内与 subscribe 投递同一引用）。 */
  snapshot = (): DBShape => {
    const v = this.runtime.version
    if (this.dbCache !== null && this.dbCacheVersion === v) return this.dbCache
    const db = this.buildDB(this.runtime.snapshot())
    this.dbCache = db
    this.dbCacheVersion = v
    return db
  }

  /** 使 DBShape / 会话缓存失效（本地元数据变更后调用，强制下帧重建）。 */
  invalidate(): void {
    this.dbCacheVersion = -1
    this.sessionCacheVersion = -1
  }

  private buildDB(snapshot: AppSnapshot): DBShape {
    const db = deriveDB(snapshot, this.meta)
    // 当前任务失效（被删/未选）时回退到第一个，保证 currentTask 非空引用稳定
    if (db.tasks.length > 0 && !db.tasks.some((t) => t.id === db.meta.curTaskId)) {
      db.meta.curTaskId = db.tasks[0]!.id
    }
    return db
  }

  // ── 选中会话快照（对话流数据源）──

  /** 当前选中目标会话的实时快照（无则 null）。 */
  currentSession = (): SessionSnapshot | null => {
    const v = this.runtime.version
    if (this.sessionCacheVersion === v) return this.sessionCache
    const snap = this.runtime.snapshot()
    const s = snap.selectedId === null
      ? null
      : snap.engagement.sessions.find((x) => x.sessionId === snap.selectedId) ?? null
    this.sessionCache = s
    this.sessionCacheVersion = v
    return s
  }

  /** 订阅选中会话变化：以快照版本号去抖，引用稳定，符合 useSyncExternalStore 契约。 */
  subscribeSession = (listener: (s: SessionSnapshot | null) => void): (() => void) => {
    let lastVersion = -1
    let last: SessionSnapshot | null = null
    return this.runtime.subscribe((snap) => {
      const v = this.runtime.version
      if (v === lastVersion) {
        listener(last)
        return
      }
      lastVersion = v
      const s = snap.selectedId === null
        ? null
        : snap.engagement.sessions.find((x) => x.sessionId === snap.selectedId) ?? null
      this.sessionCache = s
      this.sessionCacheVersion = v
      last = s
      listener(s)
    })
  }

  /** WS 连接状态。 */
  link = (): AppSnapshot['link'] => {
    return this.runtime.snapshot().link
  }

  /** 当前活动集群的目标会话列表（任务行展开用；taskId 未激活时为空）。 */
  clusterSessions = (taskId: string): ReadonlyArray<{ sessionId: string; target: string; status: SessionSnapshot['status'] }> => {
    const snap = this.runtime.snapshot()
    if (snap.activeTaskId !== taskId) return []
    return snap.engagement.sessions.map((s) => ({
      sessionId: s.sessionId,
      target: s.header?.target ?? s.sessionId,
      status: s.status,
    }))
  }

  // ── 任务目录动作（真实 RPC）──

  /** 新建任务：即时走 run；定时/周期暂按即时创建并记录本地调度元数据（后端无 schedule RPC）。 */
  createTask = async (cfg: { name: string; target: string; mode: Task['mode']; sched: Task['sched']; next: string }): Promise<void> => {
    await this.runtime.run({
      title: cfg.name,
      allowedTargets: [cfg.target],
      ...(cfg.mode === 'pt' ? {} : { maxIntensity: cfg.mode === 'scan' ? 'passive' : 'active' }),
    })
    // run 应答后 engagementId 在快照里；记录本地模式/调度元数据
    const snap = this.runtime.snapshot()
    const created = snap.tasks.find((t) => t.title === cfg.name)
    if (created !== undefined) {
      this.patchMeta(created.engagementId, { mode: cfg.mode, sched: cfg.sched, next: cfg.next })
      this.selectMeta(created.engagementId, cfg.mode)
    }
  }

  /** 选中任务（切会话）。 */
  selectTask = (id: string): void => {
    this.runtime.selectTask(id)
    this.selectMeta(id, this.meta.tasks[id]?.mode ?? 'pt')
  }

  /** 选中目标会话（任务行展开子项点击，steer/审批粒度）。 */
  selectSession = (sessionId: string): void => {
    this.runtime.select(sessionId)
    this.invalidate()
  }

  /** 删除任务（deleteEngagement RPC）。 */
  deleteTask = async (id: string): Promise<void> => {
    await this.runtime.deleteTask(id)
    this.removeMeta(id)
  }

  /** 重命名任务（renameEngagement RPC）。 */
  renameTask = async (id: string, name: string): Promise<void> => {
    await this.runtime.renameTask(id, name)
  }

  /** 调度保存：写本地元数据（后端无 schedule RPC；即时触发若当前软停则仅改展示分组）。 */
  saveSched = (id: string, s: { sched: Task['sched']; next: string }): void => {
    this.patchMeta(id, { sched: s.sched, next: s.next, stopped: s.sched !== 'now' ? true : undefined })
  }

  /** 作战模式应用：写本地元数据（后端无作战模式语义，属前端推理策略展示）。 */
  applyMode = (id: string, mode: Task['mode']): void => {
    this.patchMeta(id, { mode })
    this.selectMeta(id, mode)
  }

  /** 会话指令（steer RPC）。 */
  send = (text: string): void => {
    this.runtime.submitOrSend(text)
  }

  /** 审批应答（respond RPC）。 */
  respond = (rpcId: string, decision: 'allow' | 'deny', comment?: string): void => {
    this.runtime.respond(rpcId, decision, comment)
  }

  // ── 本地元数据维护 ──

  private patchMeta(id: string, patch: TaskMetaEntry): void {
    this.meta = {
      ...this.meta,
      tasks: { ...this.meta.tasks, [id]: { ...this.meta.tasks[id], ...patch } },
    }
    saveMeta(this.meta)
    this.invalidate()
    this.runtime.refreshTasksExternal()
  }

  private selectMeta(id: string, mode: Task['mode']): void {
    this.meta = { ...this.meta, curTaskId: id, curMode: mode }
    saveMeta(this.meta)
    this.invalidate()
  }

  private removeMeta(id: string): void {
    const tasks = { ...this.meta.tasks }
    delete tasks[id]
    this.meta = {
      ...this.meta,
      tasks,
      curTaskId: this.meta.curTaskId === id ? '' : this.meta.curTaskId,
    }
    saveMeta(this.meta)
    this.invalidate()
  }
}
