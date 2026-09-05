/**
 * Engagement：跨目标的聚合运行时视图（v4 §6 F1 runtime/engagement.ts）。
 *
 * 一个任务书 = 一个 Engagement + N 个目标 Session（多目标并行）。本对象持有
 * 属于本 engagement 的 Session 集群（host/session-* 帧登记/移除），并把每目标
 * 快照聚合为 EngagementSnapshot：状态计数、挂起审批、报告就绪数、成长指标
 * （L6 projection.growth）、最后事件时间。
 *
 * 与 Session 相同：常驻 + subscribe 快照 + deriveSnapshot 纯投影。
 */

import type { EngagementHeader, HostFrame, MuxFrame, SessionHeader, SessionStatus } from '../connection/api.ts'
import { Session } from './session.ts'
import type { SessionSnapshot } from './session.ts'

export interface EngagementSnapshot {
  engagementId: string
  header: EngagementHeader | null
  /** host 流标记过 engagement-changed（上层 F3 列表需重新拉取）。 */
  dirty: boolean
  sessions: readonly SessionSnapshot[]
  statusCounts: Record<SessionStatus, number>
  totals: {
    targets: number
    running: number
    awaitingApproval: number
    completed: number
    failed: number
  }
  pendingApprovals: number
  /** 已有 report projection 的目标数（R5 报告投影）。 */
  reportReady: number
  /** 各目标的成长度量投影（L6 projection.growth）。 */
  growth: ReadonlyArray<{ sessionId: string; metrics: unknown }>
  lastEventAt: string | null
}

const EMPTY_STATUS_COUNTS: Record<SessionStatus, number> = {
  idle: 0,
  running: 0,
  awaiting_approval: 0,
  completed: 0,
  failed: 0,
  stopped: 0,
}

type EngagementListener = (snapshot: EngagementSnapshot) => void

export class Engagement {
  readonly engagementId: string
  private headerValue: EngagementHeader | null
  private dirtyValue = false
  private readonly sessionsValue = new Map<string, Session>()
  private readonly listeners = new Set<EngagementListener>()
  private snapshotCache: EngagementSnapshot | null = null

  constructor(engagementId: string, header?: EngagementHeader) {
    this.engagementId = engagementId
    this.headerValue = header ?? null
  }

  /** 目标会话访问器（无则 undefined；常驻 Map 内）。 */
  session(sessionId: string): Session | undefined {
    return this.sessionsValue.get(sessionId)
  }

  /** 当前目标会话 id 列表。 */
  sessionIds(): readonly string[] {
    return [...this.sessionsValue.keys()]
  }

  /**
   * 登记/更新一个目标会话（run 应答或 host/session-added 后调用）。
   * 仅接受 engagementId 缺失或匹配本 engagement 的 header。
   * @returns 登记后的 Session；header 属于别的 engagement 时返回 null。
   */
  upsertSession(header: SessionHeader): Session | null {
    if (header.engagementId !== undefined && header.engagementId !== this.engagementId) return null
    let session = this.sessionsValue.get(header.sessionId)
    if (session === undefined) {
      session = new Session(header.sessionId, header)
      this.sessionsValue.set(header.sessionId, session)
    } else {
      session.applyHostFrame({ type: 'host/session-added', session: header })
    }
    this.touch()
    return session
  }

  /** 移除目标会话（host/session-removed）。 */
  removeSession(sessionId: string): void {
    if (!this.sessionsValue.delete(sessionId)) return
    this.touch()
  }

  /** 消费 host 流帧：登记/移除/状态本 engagement 的目标，或标记 engagement-changed。 */
  applyHostFrame(frame: HostFrame): void {
    switch (frame.type) {
      case 'host/session-added': {
        this.upsertSession(frame.session)
        return
      }
      case 'host/session-removed': {
        this.removeSession(frame.sessionId)
        return
      }
      case 'host/session-status': {
        const session = this.sessionsValue.get(frame.sessionId)
        if (session === undefined) return
        session.applyHostFrame(frame)
        this.touch()
        return
      }
      case 'host/engagement-changed': {
        if (frame.engagementId !== this.engagementId) return
        // 通知上层重新拉取（F3 由 SessionManager 消费；本层只置脏标记）
        this.dirtyValue = true
        this.touch()
        return
      }
      default:
        return // stream/error 由 controller 打印
    }
  }

  /** 消费 mux 流帧：按 sessionId 路由到目标 Session（未知 id 交由上层先登记）。 */
  applyMuxFrame(frame: MuxFrame): void {
    if (!('sessionId' in frame)) return
    const session = this.sessionsValue.get(frame.sessionId)
    if (session === undefined) return
    session.applyMuxFrame(frame)
    this.touch()
  }

  /** 消费 engagement-changed 的脏标记（上层拉取成功后调用）。 */
  acknowledgeDirty(): void {
    if (!this.dirtyValue) return
    this.dirtyValue = false
    this.touch()
  }

  /**
   * 订阅聚合快照：立即投递当前值，此后每次变更增量投递。
   * @returns 退订函数。
   */
  subscribe(listener: EngagementListener): () => void {
    this.listeners.add(listener)
    this.deliver(listener, this.snapshot())
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** 当前聚合快照（缓存到下次状态变更）。 */
  snapshot(): EngagementSnapshot {
    if (this.snapshotCache === null) this.snapshotCache = this.deriveSnapshot()
    return this.snapshotCache
  }

  /** 纯投影：把本 engagement 目标会话快照聚合为一张只读视图。 */
  deriveSnapshot(): EngagementSnapshot {
    const statusCounts = { ...EMPTY_STATUS_COUNTS }
    const totals = { targets: 0, running: 0, awaitingApproval: 0, completed: 0, failed: 0 }
    let pendingApprovals = 0
    let reportReady = 0
    let lastEventAt: string | null = null
    const growth: Array<{ sessionId: string; metrics: unknown }> = []
    const sessions: SessionSnapshot[] = []

    for (const session of this.sessionsValue.values()) {
      const snap = session.snapshot()
      sessions.push(snap)
      totals.targets += 1
      statusCounts[snap.status] += 1
      if (snap.status === 'running') totals.running += 1
      else if (snap.status === 'awaiting_approval') totals.awaitingApproval += 1
      else if (snap.status === 'completed') totals.completed += 1
      else if (snap.status === 'failed') totals.failed += 1
      pendingApprovals += snap.pendingApprovals.length
      if (snap.projections.report !== undefined) reportReady += 1
      if (snap.projections.growth !== undefined) {
        growth.push({ sessionId: snap.sessionId, metrics: snap.projections.growth })
      }
      if (snap.lastEventAt !== null && (lastEventAt === null || snap.lastEventAt > lastEventAt)) {
        lastEventAt = snap.lastEventAt
      }
    }

    return {
      engagementId: this.engagementId,
      header: this.headerValue,
      dirty: this.dirtyValue,
      sessions,
      statusCounts,
      totals,
      pendingApprovals,
      reportReady,
      growth,
      lastEventAt,
    }
  }

  private touch(): void {
    this.snapshotCache = null
    const snapshot = this.snapshot()
    for (const listener of [...this.listeners]) this.deliver(listener, snapshot)
  }

  private deliver(listener: EngagementListener, snapshot: EngagementSnapshot): void {
    try {
      listener(snapshot)
    } catch (cause) {
      console.error('[secai-engagement] 快照监听器异常（隔离）', cause)
    }
  }
}
