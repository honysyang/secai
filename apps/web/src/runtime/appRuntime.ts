/**
 * AppRuntime：顶层状态接线（F2/F3 集成层，真枪实弹版）。
 *
 * 职责：持有多个任务书集群（engagementId → Engagement），把下行帧路由到
 * 对象并投递聚合快照，供 App 层经 useSyncExternalStore 订阅后以 props 分发；
 * 同时承载用户动作（selectTask/select/send/respond/run/updateRisk/generateReport）。
 *
 * 数据源 = 真实后端：启动 ConnectionController（F1 双 WS + 退避重连），
 * mux/host 帧路由到对应任务书集群的 Session；握手成功后 bootstrap 拉取
 * engagements/targets/assets/risks/reports 全量落账；此后靠 WS 增量帧与
 * host/engagement-changed 触发的去抖刷新保持新鲜。send/respond 走真实 RPC。
 */

import type {
  AssetEntry,
  EngagementHeader,
  HostFrame,
  MuxFrame,
  RiskEntry,
  ReportEntry,
  SessionHeader,
  TaskBrief,
} from '../connection/api.ts'
import { ApiClient, ConnectionController } from '../connection/connection.ts'
import type { ConnectionState } from '../connection/connection.ts'
import { Engagement } from './engagement.ts'
import type { EngagementSnapshot } from './engagement.ts'

/** 顶层导航路由：工作台 = 对话面板，资产/风险/报告 = 独立管理视图。 */
export type RouteKey = 'workbench' | 'assets' | 'risks' | 'reports'

export type LinkState = 'connecting' | 'connected' | 'reconnecting'

/** 任务列表行（engagement 行头投影；仿 dsh 会话列表的顶层条目）。 */
export interface TaskSummary {
  engagementId: string
  title: string
  status: 'running' | 'completed' | 'failed'
  createdAt: string
  updatedAt: string
  sessionCount: number
}

/** 空集群快照（无活动任务书时对话面板的空态数据面）。 */
export const EMPTY_ENGAGEMENT_SNAPSHOT: EngagementSnapshot = {
  engagementId: '',
  header: null,
  dirty: false,
  sessions: [],
  statusCounts: { idle: 0, running: 0, awaiting_approval: 0, completed: 0, failed: 0, stopped: 0 },
  totals: { targets: 0, running: 0, awaitingApproval: 0, completed: 0, failed: 0 },
  pendingApprovals: 0,
  reportReady: 0,
  growth: [],
  lastEventAt: null,
}

/** 顶层快照：任务列表 + 活动集群聚合 + 选中会话 + 连接徽章 + 路由 + 全局清单。 */
export interface AppSnapshot {
  /** 任务列表（updatedAt 降序 = 最近任务在前）。 */
  tasks: readonly TaskSummary[]
  /** 当前任务书集群 id（null = 尚未选择）。 */
  activeTaskId: string | null
  /** 活动集群聚合快照（activeTaskId 为 null 时 = EMPTY_ENGAGEMENT_SNAPSHOT）。 */
  engagement: EngagementSnapshot
  selectedId: string | null
  link: LinkState
  route: RouteKey
  /** 资产管理视图数据（host/assets 帧 + bootstrap 拉取）。 */
  assets: readonly AssetEntry[]
  /** 风险管理视图数据（host/risks 帧 + bootstrap 拉取）。 */
  risks: readonly RiskEntry[]
  /** 报告管理视图数据（host/reports 帧 + bootstrap 拉取）。 */
  reports: readonly ReportEntry[]
}

type AppListener = (snapshot: AppSnapshot) => void

export class AppRuntime {
  private readonly clusters = new Map<string, Engagement>()
  private activeTaskIdValue: string | null = null
  selectedId: string | null = null
  private linkValue: LinkState = 'connecting'
  /** LLM key 配置状态（describe 握手后落账）。 */
  private llmValue: boolean | null = null
  /** 当前顶层路由（默认工作台 = 对话面板）。 */
  private routeValue: RouteKey = 'workbench'
  private tasksValue: readonly TaskSummary[] = []
  private assetsValue: readonly AssetEntry[] = []
  private risksValue: readonly RiskEntry[] = []
  private reportsValue: readonly ReportEntry[] = []
  private readonly listeners = new Set<AppListener>()
  private snapshotCache: AppSnapshot | null = null
  private started = false
  private controller: ConnectionController | null = null
  /** host/engagement-changed 去抖刷新句柄（任务列表重拉）。 */
  private refreshTimer: number | null = null
  private readonly api = new ApiClient()

  /** LLM 配置状态探针（NewEngagementModal 提交前校验用；响应式经快照订阅）。 */
  get llmConfigured(): boolean | null {
    return this.llmValue
  }

  constructor() {
    // StrictMode 下 effect 清理会把 start/stop 当普通函数调用导致 this 丢失，
    // 构造时绑定兜底。
    this.start = this.start.bind(this)
    this.stop = this.stop.bind(this)
  }

  /** 幂等启动：双 WS 连接 + 严格握手 + bootstrap 全量拉取。 */
  start(): void {
    if (this.started) return
    this.started = true
    this.controller = new ConnectionController(this.api, {
      onMuxFrame: (frame) => this.applyMuxFrame(frame),
      onHostFrame: (frame) => this.applyHostFrame(frame),
      onConnected: (description) => {
        // describe 握手成功：LLM 配置状态落账（响应式，UI 徽章随动）+ 全量引导
        this.setLlmConfigured(description.llmConfigured === true)
        this.setLink('connected')
        void this.bootstrap()
      },
      onStateChange: (state: ConnectionState) => this.setLink(state),
    })
    this.controller.start()
  }

  /** 停止数据源调度（StrictMode 开发双挂载时回收首个实例）。 */
  stop(): void {
    this.controller?.stop()
    this.controller = null
    if (this.refreshTimer !== null) {
      window.clearTimeout(this.refreshTimer)
      this.refreshTimer = null
    }
    this.started = false
    this.listeners.clear()
    this.snapshotCache = null
  }

  // ───────────────────────── 引导 / 任务列表刷新 ─────────────────────────

  /** 握手成功后的全量引导：任务列表 + 全部目标会话 + 三类全局清单。 */
  private async bootstrap(): Promise<void> {
    try {
      const [engagements, targets, assets, risks, reports] = await Promise.all([
        this.api.call('engagements', {}),
        this.api.call('targets', {}),
        this.api.call('assets', {}),
        this.api.call('risks', {}),
        this.api.call('reports', {}),
      ])
      for (const header of targets) {
        this.registerSession(header)
      }
      this.tasksValue = this.summarize(engagements)
      this.assetsValue = assets.assets
      this.risksValue = risks.risks
      this.reportsValue = reports.reports
      this.autoSelect()
      this.touch()
    } catch (cause) {
      // 引导失败不拖垮 UI：空态呈现，重连后 describe → bootstrap 重试
      console.warn('[secai-app] bootstrap 失败（隔离，等待重连重试）', cause)
    }
  }

  /** host/engagement-changed → 去抖 300ms 重拉任务列表（会话状态联动任务状态）。 */
  private scheduleTasksRefresh(): void {
    if (this.refreshTimer !== null) return
    this.refreshTimer = window.setTimeout(() => {
      this.refreshTimer = null
      void this.refreshTasks()
    }, 300)
  }

  private async refreshTasks(): Promise<void> {
    try {
      const engagements = await this.api.call('engagements', {})
      this.tasksValue = this.summarize(engagements)
      if (this.activeTaskIdValue === null) this.autoSelect()
      this.touch()
    } catch (cause) {
      console.warn('[secai-app] 任务列表刷新失败（隔离）', cause)
    }
  }

  /** engagements RPC 行头 → TaskSummary 列表（updatedAt 降序）。 */
  private summarize(rows: readonly EngagementHeader[]): readonly TaskSummary[] {
    const countByEngagement = new Map<string, number>()
    for (const cluster of this.clusters.values()) {
      const id = cluster.engagementId
      countByEngagement.set(id, (countByEngagement.get(id) ?? 0) + cluster.sessionIds().length)
    }
    return [...rows]
      .map((row) => ({
        engagementId: row.engagementId,
        title: row.title,
        status: row.status,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
        sessionCount: countByEngagement.get(row.engagementId) ?? 0,
      }))
      .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0))
  }

  // ───────────────────────── 集群路由 ─────────────────────────

  /** 取/建任务书集群（engagementId → Engagement 常驻）。 */
  private clusterFor(engagementId: string): Engagement {
    let cluster = this.clusters.get(engagementId)
    if (cluster === undefined) {
      cluster = new Engagement(engagementId)
      this.clusters.set(engagementId, cluster)
    }
    return cluster
  }

  /** 登记一个目标会话（bootstrap/host/session-added/run 应答共用）。 */
  private registerSession(header: SessionHeader): void {
    const engagementId = header.engagementId ?? ''
    if (engagementId === '') return
    this.clusterFor(engagementId).upsertSession(header)
  }

  /** 按 sessionId 找所属集群（mux 帧路由用；未知 id 返回 undefined）。 */
  private clusterOfSession(sessionId: string): Engagement | undefined {
    for (const cluster of this.clusters.values()) {
      if (cluster.session(sessionId) !== undefined) return cluster
    }
    return undefined
  }

  /** 空态自动选择：无选中会话时挑活动任务里 running 的会话，否则第一个。 */
  private autoSelect(): void {
    if (this.selectedId !== null && this.clusterOfSession(this.selectedId) !== undefined) return
    if (this.activeTaskIdValue === null) {
      const latest = this.tasksValue[0]
      if (latest !== undefined) this.activeTaskIdValue = latest.engagementId
    }
    if (this.activeTaskIdValue === null) return
    const cluster = this.clusters.get(this.activeTaskIdValue)
    if (cluster === undefined) return
    const sessions = cluster.snapshot().sessions
    if (sessions.length === 0) {
      this.selectedId = null
      return
    }
    const running = sessions.find((session) => session.status === 'running')
    this.selectedId = (running ?? sessions[0]!).sessionId
  }

  // ───────────────────────── 帧消费 ─────────────────────────

  /** 消费 mux 流帧：按 sessionId 路由到所属任务书集群（未知会话静默丢弃）。 */
  applyMuxFrame(frame: MuxFrame): void {
    if (!('sessionId' in frame)) return
    const cluster = this.clusterOfSession(frame.sessionId)
    if (cluster === undefined) return
    cluster.applyMuxFrame(frame)
    this.touch()
  }

  /** 消费 host 流帧：集群登记/清单落账；engagement-changed 触发任务列表去抖刷新。 */
  applyHostFrame(frame: HostFrame): void {
    switch (frame.type) {
      case 'host/assets':
        this.assetsValue = frame.assets
        this.touch()
        return
      case 'host/risks':
        this.risksValue = frame.risks
        this.touch()
        return
      case 'host/reports':
        this.reportsValue = frame.reports
        this.touch()
        return
      case 'host/session-added':
        this.registerSession(frame.session)
        this.scheduleTasksRefresh()
        // 新会话落在当前活动任务里且尚无选中 → 自动接上（贴近 dsh 新会话即选中）
        if (this.selectedId === null) this.autoSelect()
        this.touch()
        return
      case 'host/session-removed':
        this.scheduleTasksRefresh()
        this.touch()
        return
      case 'host/session-status':
        this.clusterOfSession(frame.sessionId)?.applyHostFrame(frame)
        this.touch()
        return
      case 'host/engagement-changed':
        this.scheduleTasksRefresh()
        return
      default:
        return // stream/error 由 controller 打印
    }
  }

  // ───────────────────────── 用户动作 ─────────────────────────

  /** 选中目标会话（会话所在任务书成为活动任务；即回工作台对话面板）。 */
  select(sessionId: string): void {
    const cluster = this.clusterOfSession(sessionId)
    if (cluster === undefined) return
    this.activeTaskIdValue = cluster.engagementId
    this.selectedId = sessionId
    this.routeValue = 'workbench'
    this.touch()
  }

  /** 选中任务（任务列表行点击）：切换活动任务并自动选中其首个目标会话。 */
  selectTask(engagementId: string): void {
    if (this.activeTaskIdValue === engagementId && this.selectedId !== null) {
      this.routeValue = 'workbench'
      this.touch()
      return
    }
    this.activeTaskIdValue = engagementId
    this.selectedId = null
    this.autoSelect()
    this.routeValue = 'workbench'
    this.touch()
  }

  /** 切换顶层导航路由。 */
  setRoute(route: RouteKey): void {
    if (this.routeValue === route) return
    this.routeValue = route
    this.touch()
  }

  /**
   * 智能提交：有选中会话 → steer 指令；无会话 → 用自然语言作任务书 run。
   * 用户可在对话框直接下达任务，无需先打开「新建任务」弹窗。
   */
  async submitOrSend(text: string): Promise<void> {
    if (this.selectedId !== null) {
      this.send(text)
      return
    }
    await this.run({ allowedTargets: [text] })
  }

  /**
   * 下发任务书（NewEngagementModal / 对话框直接下达）：POST /api/run 并把应答
   * 登记的目标会话并入对应集群（选中首个）；会话事件走 WS 下行流。
   */
  async run(brief: TaskBrief): Promise<void> {
    const response = await this.api.call('run', brief)
    for (const [index, sessionId] of response.sessionIds.entries()) {
      this.registerSession({
        sessionId,
        target: brief.allowedTargets[index] ?? sessionId,
        status: 'running',
        engagementId: response.engagementId,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      })
    }
    void this.refreshTasks()
    const first = response.sessionIds[0]
    if (first !== undefined) this.select(first)
  }

  /** 指令（InputBar）：steer RPC；回执/失败均落本地 system 行。 */
  send(text: string): void {
    const sessionId = this.selectedId
    if (sessionId === null) return
    void this.api
      .call('steer', { sessionId, instruction: text })
      .then(() => {
        // 回执闭环：服务端确认收到 → 本地落一条 system 回执气泡（✓ 指令已下发）
        this.appendLocalSystemEvent(sessionId, `✓ 指令已下发（${sessionId}）`)
      })
      .catch((cause) => {
        // 失败不静默：会话内红字提示（session_inactive / unknown_session 等）
        const message = cause instanceof Error ? cause.message : String(cause)
        console.warn('[secai-app] steer 失败（隔离）', cause)
        this.appendLocalSystemEvent(sessionId, `✗ 指令下发失败：${message}`, 'error')
      })
  }

  /**
   * 本地追加一条 system 事件（不入服务端，仅前端视图层回执展示）。
   * 借道 session/event 帧进对象层事件窗口，ChatView 的 SystemRow 原样呈现。
   */
  private appendLocalSystemEvent(sessionId: string, text: string, tone: 'ok' | 'error' = 'ok'): void {
    this.applyMuxFrame({
      type: 'session/event',
      sessionId,
      data: {
        eventId: `local-steer-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        // 时间戳作 seq：保持事件窗口内单调（真实帧 seq 远小于毫秒时间戳）
        seq: Date.now(),
        type: 'system',
        data: { text, tone },
        createdAt: new Date().toISOString(),
      },
    })
  }

  /** 审批应答（ApprovalCard）：POST /api/respond，rpcId 回响。 */
  respond(rpcId: string, decision: 'allow' | 'deny', comment?: string): void {
    void this.api
      .respond({ rpcId, decision, ...(comment !== undefined ? { comment } : {}) })
      .catch((cause) => {
        console.warn('[secai-app] respond 失败（隔离）', cause)
      })
  }

  /** 风险处理状态流转（RiskPanel）：updateRisk RPC → 应答条目落账（host/risks 帧兜底）。 */
  async updateRiskStatus(riskId: string, status: RiskEntry['status']): Promise<void> {
    const response = await this.api.call('updateRisk', { riskId, status })
    this.risksValue = this.risksValue.map((risk) => (risk.id === response.risk.id ? response.risk : risk))
    this.touch()
  }

  /** 生成渗透报告（ReportPanel）：report RPC → 重拉报告清单（产物/风险账本联动在服务端）。 */
  async generateReport(engagementId: string): Promise<void> {
    await this.api.call('report', { engagementId })
    const reports = await this.api.call('reports', {})
    this.reportsValue = reports.reports
    const risks = await this.api.call('risks', {})
    this.risksValue = risks.risks
    this.touch()
  }

  // ───────────────────────── 订阅 / 快照 ─────────────────────────

  subscribe = (listener: AppListener): (() => void) => {
    this.listeners.add(listener)
    this.deliver(listener, this.snapshot())
    return () => {
      this.listeners.delete(listener)
    }
  }

  snapshot = (): AppSnapshot => {
    if (this.snapshotCache === null) this.snapshotCache = this.deriveSnapshot()
    return this.snapshotCache
  }

  private deriveSnapshot(): AppSnapshot {
    const active = this.activeTaskIdValue !== null ? this.clusters.get(this.activeTaskIdValue) : undefined
    return {
      tasks: this.tasksValue,
      activeTaskId: this.activeTaskIdValue,
      engagement: active !== undefined ? active.snapshot() : EMPTY_ENGAGEMENT_SNAPSHOT,
      selectedId: this.selectedId,
      link: this.linkValue,
      route: this.routeValue,
      assets: this.assetsValue,
      risks: this.risksValue,
      reports: this.reportsValue,
    }
  }

  private setLlmConfigured(configured: boolean): void {
    if (this.llmValue === configured) return
    this.llmValue = configured
    this.touch()
  }

  private setLink(link: LinkState): void {
    if (this.linkValue === link) return
    this.linkValue = link
    this.touch()
  }

  private touch(): void {
    this.snapshotCache = null
    const snapshot = this.snapshot()
    for (const listener of [...this.listeners]) this.deliver(listener, snapshot)
  }

  private deliver(listener: AppListener, snapshot: AppSnapshot): void {
    try {
      listener(snapshot)
    } catch (cause) {
      console.error('[secai-app] 快照监听器异常（隔离）', cause)
    }
  }
}
