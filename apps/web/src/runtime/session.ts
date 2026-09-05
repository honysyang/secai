/**
 * Session：单个目标会话的常驻运行时对象（v4 §6 F1 runtime/session.ts）。
 *
 * 语义（对照 dsh runtime Session）：
 * - 常驻：Session 生命周期 = 会话集群生命周期，创建后不回销毁；连接代际重建只
 *   替换 ConnectionController，Session 继续消费两流帧（离线/重连期间的帧经
 *   history 回填补齐）。
 * - 事件窗口：events 是有界窗口（EVENT_WINDOW_LIMIT），超限丢最旧；lastSeq
 *   单调前进，subscribe 帧即回放完成基线。
 * - subscribe 快照：subscribe 立刻投递当前快照，此后每次 apply 后增量投递；
 *   监听器异常隔离，不拖垮帧泵。
 * - deriveSnapshot：纯投影函数——队列按 priority 降序、审批挂起/日志、projection
 *   合并视图（one-higher-seq-wins）都从这里导出。
 *
 * 无 React 依赖（对象层）。
 */

import type {
  ApprovalRequest,
  ApprovalResolution,
  HostFrame,
  Job,
  MuxFrame,
  QueueItem,
  SessionEvent,
  SessionHeader,
  SessionStatus,
} from '../connection/api.ts'

/** 会话事件窗口上限：超出丢最旧（历史可经 reconnect 回填，窗口只是视图）。 */
export const EVENT_WINDOW_LIMIT = 500
/** 审批日志窗口上限。 */
export const APPROVAL_LOG_LIMIT = 50

/** 挂起中的审批（等待人工 respond）。 */
export interface PendingApprovalView {
  rpcId: string
  payload: ApprovalRequest
}

/** 已决审批日志条目（resolution 到达即落账；request 缺失=窗口外请求的决议）。 */
export interface ApprovalLogEntry {
  rpcId: string
  request?: ApprovalRequest
  resolution: ApprovalResolution
}

/** Session 对外快照：只读数据面，deriveSnapshot 的产物。 */
export interface SessionSnapshot {
  sessionId: string
  header: SessionHeader | null
  status: SessionStatus
  removed: boolean
  subscribed: boolean
  lastSeq: number
  events: readonly SessionEvent[]
  queue: readonly QueueItem[]
  jobs: readonly Job[]
  projections: Readonly<Record<string, unknown>>
  pendingApprovals: readonly PendingApprovalView[]
  approvalLog: readonly ApprovalLogEntry[]
  counts: {
    events: number
    pendingApprovals: number
    resolvedApprovals: number
  }
  lastEventAt: string | null
}

type SessionListener = (snapshot: SessionSnapshot) => void

export class Session {
  readonly sessionId: string
  private headerValue: SessionHeader | null = null
  private removedValue = false
  private subscribedValue = false
  private lastSeqValue = 0
  private projectionSeqValue = -1
  private readonly eventsValue: SessionEvent[] = []
  private readonly projectionsValue = new Map<string, unknown>()
  private queueValue: readonly QueueItem[] = []
  private jobsValue: readonly Job[] = []
  private readonly pendingApprovalsValue = new Map<string, ApprovalRequest>()
  private readonly approvalLogValue: ApprovalLogEntry[] = []
  private readonly listeners = new Set<SessionListener>()
  private snapshotCache: SessionSnapshot | null = null

  constructor(sessionId: string, header?: SessionHeader) {
    this.sessionId = sessionId
    this.headerValue = header ?? null
  }

  /** 消费 host 流帧（session-added/removed/status）；全局帧留给 manager。 */
  applyHostFrame(frame: HostFrame): void {
    switch (frame.type) {
      case 'host/session-added': {
        if (frame.session.sessionId !== this.sessionId) return
        this.headerValue = frame.session
        this.removedValue = false
        break
      }
      case 'host/session-removed': {
        if (frame.sessionId !== this.sessionId) return
        this.removedValue = true
        break
      }
      case 'host/session-status': {
        if (frame.sessionId !== this.sessionId) return
        if (this.headerValue !== null) {
          this.headerValue = {
            ...this.headerValue,
            status: frame.status,
            updatedAt: new Date().toISOString(),
          }
        }
        break
      }
      default:
        // host/engagement-changed / stream/error：controller 与 Engagement 层处理
        return
    }
    this.touch()
  }

  /** 消费 mux 流帧；sessionId 不匹配（被错误路由）的帧静默丢弃。 */
  applyMuxFrame(frame: MuxFrame): void {
    if ('sessionId' in frame && frame.sessionId !== this.sessionId) return
    switch (frame.type) {
      case 'session/event': {
        this.eventsValue.push(frame.data)
        if (this.eventsValue.length > EVENT_WINDOW_LIMIT) {
          this.eventsValue.splice(0, this.eventsValue.length - EVENT_WINDOW_LIMIT)
        }
        if (frame.data.seq > this.lastSeqValue) this.lastSeqValue = frame.data.seq
        break
      }
      case 'session/subscribed': {
        // 服务端回放完成基线：此后的帧是 live
        this.subscribedValue = true
        if (frame.lastSeq > this.lastSeqValue) this.lastSeqValue = frame.lastSeq
        break
      }
      case 'session/queue':
        this.queueValue = frame.items
        break
      case 'session/jobs':
        this.jobsValue = frame.jobs
        break
      case 'session/projection': {
        // one-higher-seq-wins：同 seq 的重放幂等重放，低于当前 seq 的过期帧丢弃
        if (frame.seq < this.projectionSeqValue) return
        this.projectionSeqValue = frame.seq
        for (const [key, value] of Object.entries(frame.values)) {
          this.projectionsValue.set(key, value)
        }
        break
      }
      case 'approval/requested':
        this.pendingApprovalsValue.set(frame.rpcId, frame.payload)
        break
      case 'approval/resolved': {
        const request = this.pendingApprovalsValue.get(frame.rpcId)
        this.pendingApprovalsValue.delete(frame.rpcId)
        if (this.approvalLogValue.some((entry) => entry.rpcId === frame.rpcId)) break
        this.approvalLogValue.push({
          rpcId: frame.rpcId,
          ...(request === undefined ? {} : { request }),
          resolution: frame.payload,
        })
        if (this.approvalLogValue.length > APPROVAL_LOG_LIMIT) {
          this.approvalLogValue.splice(0, this.approvalLogValue.length - APPROVAL_LOG_LIMIT)
        }
        break
      }
      default:
        // stream/error：controller 打印告警即可
        return
    }
    this.touch()
  }

  /**
   * 订阅快照：立即投递当前快照，此后每次状态变更增量投递。
   * @returns 退订函数。
   */
  subscribe(listener: SessionListener): () => void {
    this.listeners.add(listener)
    this.deliver(listener, this.snapshot())
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** 当前快照（缓存到下次状态变更）。 */
  snapshot(): SessionSnapshot {
    if (this.snapshotCache === null) this.snapshotCache = this.deriveSnapshot()
    return this.snapshotCache
  }

  /** 纯投影：从内部状态派生一份只读快照（subscribe 快照与测试共用）。 */
  deriveSnapshot(): SessionSnapshot {
    const pendingApprovals: PendingApprovalView[] = []
    for (const [rpcId, payload] of this.pendingApprovalsValue) pendingApprovals.push({ rpcId, payload })
    const last = this.eventsValue.length === 0 ? null : this.eventsValue[this.eventsValue.length - 1]!.createdAt
    return {
      sessionId: this.sessionId,
      header: this.headerValue,
      status: this.headerValue?.status ?? 'idle',
      removed: this.removedValue,
      subscribed: this.subscribedValue,
      lastSeq: this.lastSeqValue,
      events: this.eventsValue.slice(),
      queue: this.queueValue,
      jobs: this.jobsValue,
      projections: Object.fromEntries(this.projectionsValue),
      pendingApprovals,
      approvalLog: this.approvalLogValue.slice(),
      counts: {
        events: this.eventsValue.length,
        pendingApprovals: pendingApprovals.length,
        resolvedApprovals: this.approvalLogValue.length,
      },
      lastEventAt: last,
    }
  }

  private touch(): void {
    this.snapshotCache = null
    const snapshot = this.snapshot()
    for (const listener of [...this.listeners]) this.deliver(listener, snapshot)
  }

  private deliver(listener: SessionListener, snapshot: SessionSnapshot): void {
    try {
      listener(snapshot)
    } catch (cause) {
      // 监听器异常隔离：UI 层坏了不能把帧泵带崩
      console.error('[secai-session] 快照监听器异常（隔离）', cause)
    }
  }
}
