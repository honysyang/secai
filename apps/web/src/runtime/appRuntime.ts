/**
 * AppRuntime：顶层状态接线（F2/F3 集成层）。
 *
 * 职责：持有一个 Engagement 集群（Session/Engagement 对象层），把下行帧
 * 路由到对象并投递聚合快照，供 App 层经 useSyncExternalStore 订阅后以
 * props 分发给组件；同时承载用户动作（select/send/respond）。
 *
 * 数据源由 DEMO_MODE 常量开关决定（联调时翻 false）：
 * - DEMO_MODE = true：createDemo 内嵌 mock MuxFrame/HostFrame 序列驱动
 *   （不经网络；帧经对象层真实落账，UI 完全可交互）。
 * - DEMO_MODE = false：启动 ConnectionController（F1 双 WS + 退避重连），
 *   mux/host 帧路由到本 engagement 的 Session；send/respond 走真实 RPC
 *   （api.steer / api.respond）。会话登记依赖后端 run/engagements 契约，
 *   在 sessionManager 合流前以空态呈现。
 */

import type { HostFrame, MuxFrame, TaskBrief } from '../connection/api.ts'
import type { ConnectionState } from '../connection/connection.ts'
import { ApiClient, createConnection } from '../connection/connection.ts'
import { Engagement } from './engagement.ts'
import type { EngagementSnapshot } from './engagement.ts'
import { createDemo } from './demo.ts'
import type { DemoController } from './demo.ts'
import { DEMO_ENGAGEMENT_ID } from './demo.ts'

/** 数据源开关：true = 内嵌 demo 序列（后端 R3/R4 未就绪时的默认）；联调置 false。 */
export const DEMO_MODE = true

/** 真实模式的本地聚合 engagement id（会话 header 的 engagementId 匹配此值才登记）。 */
const LIVE_ENGAGEMENT_ID = 'live-engagement'

export type LinkState = 'demo' | 'connecting' | 'connected' | 'reconnecting'

/** 顶层快照：聚合视图 + 选中会话 + 连接徽章。 */
export interface AppSnapshot {
  engagement: EngagementSnapshot
  selectedId: string | null
  link: LinkState
}

type AppListener = (snapshot: AppSnapshot) => void

export class AppRuntime {
  readonly engagement: Engagement
  selectedId: string | null = null
  private linkValue: LinkState
  /** LLM key 配置状态（describe 握手后落账；demo 模式保持 null = 未知）。 */
  private llmValue: boolean | null = null
  private readonly listeners = new Set<AppListener>()

  /** LLM 配置状态探针（NewEngagementModal 提交前校验用；响应式经快照订阅）。 */
  get llmConfigured(): boolean | null {
    return this.llmValue
  }
  private snapshotCache: AppSnapshot | null = null
  private started = false
  private demo: DemoController | null = null
  private readonly api = new ApiClient()

  constructor() {
    // StrictMode 下 effect 清理会把 start/stop 当普通函数调用导致 this 丢失，
    // 构造时绑定兜底。
    this.start = this.start.bind(this)
    this.stop = this.stop.bind(this)
    this.engagement = new Engagement(DEMO_MODE ? DEMO_ENGAGEMENT_ID : LIVE_ENGAGEMENT_ID, {
      engagementId: DEMO_MODE ? DEMO_ENGAGEMENT_ID : LIVE_ENGAGEMENT_ID,
      title: DEMO_MODE ? '渗透演练：demo.ine.local 集群侦察与提权' : '待命（等待任务书）',
      status: 'running',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    })
    this.linkValue = DEMO_MODE ? 'demo' : 'connecting'
  }

  /** 幂等启动：数据源与调度接管。 */
  start(): void {
    if (this.started) return
    this.started = true
    if (DEMO_MODE) {
      const demo = createDemo({
        applyHostFrame: (frame) => this.applyHostFrame(frame),
        applyMuxFrame: (frame) => this.applyMuxFrame(frame),
      })
      this.demo = demo
      demo.start()
      this.select('sess-a-demo')
    } else {
      createConnection(
        {
          onMuxFrame: (frame) => this.applyMuxFrame(frame),
          onHostFrame: (frame) => this.applyHostFrame(frame),
          onConnected: (description) => {
            // describe 握手成功：把 LLM 配置状态落账（响应式，UI 徽章随动）
            this.setLlmConfigured(description.llmConfigured === true)
            this.setLink('connected')
          },
          onStateChange: (state: ConnectionState) => this.setLink(state),
        },
        {},
        this.api,
      )
    }
  }

  /** 停止数据源调度（StrictMode 开发双挂载时回收首个实例的定时器）。 */
  stop(): void {
    this.demo?.stop()
    this.demo = null
    this.started = false
    this.listeners.clear()
    this.snapshotCache = null
  }

  /** 下行帧路由：mux 按 sessionId 交 Engagement（未知会话静默丢弃）；host 交 Engagement。 */
  applyMuxFrame(frame: MuxFrame): void {
    if (!('sessionId' in frame)) return
    this.engagement.applyMuxFrame(frame)
  }

  applyHostFrame(frame: HostFrame): void {
    this.engagement.applyHostFrame(frame)
  }

  select(sessionId: string | null): void {
    if (this.selectedId === sessionId) return
    this.selectedId = sessionId
    this.touch()
  }

  /**
   * 下发任务书（NewEngagementModal）：真实 = POST /api/run 并把应答登记的
   * 目标会话并入本 engagement（选中首个）；demo 模式无后端可下发，抛错由
   * 弹窗行内呈现。会话事件仍走 WS 下行流（run 不应答事件）。
   */
  async run(brief: TaskBrief): Promise<void> {
    if (this.demo !== null) {
      throw new Error('演示模式下不可下发任务书——联调时把 DEMO_MODE 置 false')
    }
    const response = await this.api.call('run', brief)
    response.sessionIds.forEach((sessionId, index) => {
      this.engagement.upsertSession({
        sessionId,
        target: brief.allowedTargets[index] ?? sessionId,
        status: 'idle',
        engagementId: response.engagementId,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      })
    })
    const first = response.sessionIds[0]
    if (first !== undefined) this.select(first)
  }

  /** 指令（InputBar）：demo = 本地模拟应答；真实 = steer RPC。 */
  send(text: string): void {
    const sessionId = this.selectedId
    if (sessionId === null) return
    if (this.demo !== null) {
      this.demo.send(sessionId, text)
      return
    }
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

  /** 审批应答（ApprovalCard）：demo = 本地裁决；真实 = POST /api/respond。 */
  respond(rpcId: string, decision: 'allow' | 'deny', comment?: string): void {
    const sessionId = this.selectedId
    if (sessionId === null) return
    if (this.demo !== null) {
      this.demo.respond(sessionId, rpcId, decision, comment)
      return
    }
    void this.api.respond({ rpcId, decision, ...(comment !== undefined ? { comment } : {}) }).catch((cause) => {
      console.warn('[secai-app] respond 失败（隔离）', cause)
    })
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
    return {
      engagement: this.engagement.snapshot(),
      selectedId: this.selectedId,
      link: this.linkValue,
      llmConfigured: this.llmValue,
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
