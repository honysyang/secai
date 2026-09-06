/**
 * SECAI-PT 前端通信契约（v4 §6 F1，与 dsh ConnectionController 语义对齐）。
 *
 * - RPC 上行：fetch POST /api/{method}，请求体 { rpcId, method, payload }；
 *   业务错误恒 HTTP 200 + { ok:false, error }，HTTP 状态只表达载体层。
 * - WS 下行（纯下行，客户端发消息会被服务端 1008 关闭）：/api/events.mux 与
 *   /api/events.host 两条流；消息即 MuxFrame / HostFrame（无 rpcId 外包装，
 *   需要回响处帧内自带 rpcId）。
 * - 审批应答：POST /api/respond，帧级 rpcId 原样回响。
 *
 * 帧协议与后端 server/ws.py、方法表与 server/api.py 对齐（R3/R4 落地，
 * F1 先冻结前端侧类型与语义）。
 */

// ───────────────────────── 上行载体 ─────────────────────────

/** 发起方递增的单次 RPC 编号；approval 应答时原样回响。 */
export type RpcId = string

/** 上行 RPC 请求体（POST body）。 */
export interface RpcRequest<P> {
  rpcId: RpcId
  method: string
  payload: P
}

/** 业务错误：code 为稳定机器码，message 面向人。 */
export interface RpcError {
  code: string
  message: string
  details?: Record<string, unknown>
}

/** 业务结果信封：成功 ok:true 带 result；业务失败恒 ok:false 带 error。 */
export type RpcResult<T> = { ok: true; result: T } | { ok: false; error: RpcError }

// ───────────────────────── 领域类型 ─────────────────────────

/** 会话状态（目标行圆点 = idle/running/awaiting_approval/completed/…）。 */
export type SessionStatus =
  | 'idle'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'stopped'

/** 假设生命周期（对齐 pentest/hypothesis.py）。 */
export type HypothesisStatus = 'pending' | 'testing' | 'validated' | 'falsified' | 'inconclusive'

/** 目标会话在 host 列表中的行头。 */
export interface SessionHeader {
  sessionId: string
  target: string
  status: SessionStatus
  engagementId?: string
  createdAt: string
  updatedAt: string
}

/** 单条会话事件（对齐 events 表：event_id/seq/type/data/created_at）。 */
export interface SessionEvent {
  eventId: string
  seq: number
  type: string
  data: Record<string, unknown>
  createdAt: string
}

/** 假设队列条目（Hypothesis 的流上视图，F3 HypothesisQueue 数据源）。 */
export interface QueueItem {
  id: string
  hypothesisId?: string
  statement: string
  status: HypothesisStatus
  priority: number
  attempts: number
}

/** 后台执行单元视图（工具调用/子任务）。 */
export interface Job {
  id: string
  kind: string
  summary: string
  status: 'queued' | 'running' | 'awaiting_approval' | 'done' | 'failed'
  createdAt: string
}

/** 审批请求（T3 人审，L5 护栏）；帧级 rpcId 用于 respond 回响。 */
export interface ApprovalRequest {
  action: string
  description: string
  detail?: Record<string, unknown>
  createdAt: string
}

/** 审批终局（服务端确认后的广播）。 */
export interface ApprovalResolution {
  decision: 'allow' | 'deny'
  comment?: string
  decidedAt: string
}

/** Engagement（任务书）行头。 */
export interface EngagementHeader {
  engagementId: string
  title: string
  status: 'running' | 'completed' | 'failed'
  createdAt: string
  updatedAt: string
}

/** describe 探测结果（严格握手的一元可达性证明）。 */
export interface HostDescription {
  product: string
  version: string
  serverTime: string
}

// ───────────────────────── 下行帧（F1 类型表，与 ws.py 对齐） ─────────────────────────

/** Mux 流帧：按 sessionId 路由到目标 Session。 */
export type MuxFrame =
  | { type: 'session/event'; sessionId: string; data: SessionEvent }
  | { type: 'session/subscribed'; sessionId: string; lastSeq: number }
  | { type: 'session/queue'; sessionId: string; items: QueueItem[] }
  | { type: 'session/jobs'; sessionId: string; jobs: Job[] }
  | { type: 'session/projection'; sessionId: string; values: Record<string, unknown>; seq: number }
  | { type: 'approval/requested'; sessionId: string; rpcId: string; payload: ApprovalRequest }
  | { type: 'approval/resolved'; sessionId: string; rpcId: string; payload: ApprovalResolution }
  | { type: 'stream/error'; message: string }

/** Host 流帧：全局（跨目标）会话集群视图。 */
export type HostFrame =
  | { type: 'host/session-added'; session: SessionHeader }
  | { type: 'host/session-removed'; sessionId: string }
  | { type: 'host/session-status'; sessionId: string; status: SessionStatus }
  | { type: 'host/engagement-changed'; engagementId: string }
  | { type: 'stream/error'; message: string }

// ───────────────────────── RPC 方法表（server/api.py 对齐） ─────────────────────────

/** 任务书输入（run 创建 engagement；字段对齐 ScopeConstraint.from_task_brief）。 */
export interface TaskBrief {
  title?: string
  allowedTargets: string[]
  excludedTargets?: string[]
  forbiddenActions?: string[]
  timeWindow?: [string, string] | null
  maxIntensity?: 'passive' | 'active' | 'aggressive'
}

/** run 应答：新建 engagement + 每目标一条 session。 */
export interface RunResponse {
  engagementId: string
  sessionIds: string[]
}

export interface SteerRequest {
  sessionId: string
  instruction: string
}

/** respond 请求：rpcId 必须回响 approval/requested 帧中的 rpcId。 */
export interface RespondRequest {
  rpcId: RpcId
  decision: 'allow' | 'deny'
  comment?: string
}

export interface ReportRequest {
  engagementId: string
}

/** 报告进度投影摘要（R5 报告引擎）。 */
export interface ReportSummary {
  engagementId: string
  status: 'drafting' | 'ready'
  sections: string[]
  totalFindings: number
  generatedAt?: string
  /** 「生成即存」落盘的产物元信息（md + json 各一份，无产物时为空数组）。 */
  artifacts?: ArtifactMeta[]
}

/** 报告产物元信息（server/artifacts.py ArtifactMeta，落盘即返回）。 */
export interface ArtifactMeta {
  artifactId: string
  engagementId: string
  format: 'md' | 'json'
  path: string
  sizeBytes: number
  createdAt: string
}

/** 方法 → { request, response } 类型映射；新增后端方法在此登记。 */
export interface ApiMethodMap {
  describe: { request: Record<string, never>; response: HostDescription }
  targets: { request: { engagementId?: string }; response: SessionHeader[] }
  engagements: { request: Record<string, never>; response: EngagementHeader[] }
  run: { request: TaskBrief; response: RunResponse }
  steer: { request: SteerRequest; response: Record<string, never> }
  respond: { request: RespondRequest; response: Record<string, never> }
  report: { request: ReportRequest; response: ReportSummary }
  listArtifacts: { request: { engagementId: string }; response: { artifacts: ArtifactMeta[] } }
  /** 导出走原始文件流（成功非 JSON 信封），不能用 call()——见 downloadReport helper。 */
  exportReport: { request: { engagementId: string; format: 'md' | 'json' }; response: Blob }
}

export type ApiMethodName = keyof ApiMethodMap

/** 方法表（运行时用途：describe 探测 / 文档/调试回显）。 */
export const API_METHODS: readonly ApiMethodName[] = [
  'describe',
  'targets',
  'engagements',
  'run',
  'steer',
  'respond',
  'report',
  'listArtifacts',
  'exportReport',
]

// ───────────────────────── 报告文件导出（绕过 JSON 信封） ─────────────────────────

/**
 * 下载报告产物：POST /api/exportReport 直接取原始文件流（成功非 200 JSON 信封，
 * 不能走 ApiClient.call）。res.ok → blob + 临时 ObjectURL 触发浏览器下载，
 * 文件名从 Content-Disposition 解析（兜底 report-<engagementId>.<fmt>）；
 * 非 ok（业务错误恒 HTTP 200 {ok:false,error}）→ 解析错误消息并抛
 * ApiBusinessError，由调用方提示。
 */
export async function downloadReport(
  engagementId: string,
  format: 'md' | 'json',
  baseUrl?: string,
): Promise<void> {
  const origin = baseUrl ?? (typeof window !== 'undefined' ? window.location.origin : '')
  const res = await fetch(`${origin}/api/exportReport`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ rpcId: 'rpc-export', method: 'exportReport', payload: { engagementId, format } }),
  })
  if (!res.ok) {
    throw new Error(`POST /api/exportReport 载体层错误 HTTP ${res.status}`)
  }
  // 业务错误恒 HTTP 200 + {ok:false,error}：先看 content-type 再决定按信封还是文件流解析
  const contentType = res.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    const envelope = (await res.json()) as RpcResult<never>
    if (!envelope.ok) {
      throw new Error(`导出失败 ${envelope.error.code}：${envelope.error.message}`)
    }
  }
  const blob = await res.blob()
  const filename = _filenameFromDisposition(res.headers.get('content-disposition'))
    ?? `report-${engagementId}.${format}`
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
  } finally {
    // 下载交给浏览器后即可回收（Chrome 会持有 blob 至落盘完成）
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }
}

/** 从 Content-Disposition 头解析 filename（attachment; filename="..." 形态）。 */
function _filenameFromDisposition(disposition: string | null): string | null {
  if (disposition === null) return null
  const match = /filename\*?=(?:UTF-8''|"|\s)?([^";\s]+)/i.exec(disposition)
  return match !== null ? decodeURIComponent(match[1] ?? '') : null
}
