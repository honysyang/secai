/**
 * SECAI-PT ConnectionController（v4 §6 F1，逐语义对齐 dsh ConnectionController）：
 *
 * 1. 每代连接开两条物理流：/api/events.mux + /api/events.host（WS 纯下行；
 *    向服务端发消息会被 1008 关闭，本层永不 send）。
 * 2. 严格握手：Promise.all([ describe({}), mux onOpen + host onOpen || 3s 超时 ])
 *    成功才发 onConnected；超时按 dsh 语义"代际照常 connected"，迟到的流由
 *    断线重连路径兜底。
 * 3. 指数退避重连：500ms 基数 ×2，封顶 10s（带 ±50% 抖动，与 dsh 一致）。
 * 4. 上行走 HTTP RPC：fetch POST /api/{method}；业务错误恒 200 + {ok:false,error}，
 *    HTTP 状态只表达载体层；审批应答 POST /api/respond（rpcId 回响）。
 * 5. 重连 = 重开双流 + 上层 history 回填（无 since-cursor 语义，见 runtime/）。
 *
 * 对象层无 React 依赖；实例由上层 runtime 装配（本模块只提供类 + 装配函数）。
 */

import type {
  ApiMethodMap,
  ApiMethodName,
  HostDescription,
  HostFrame,
  MuxFrame,
  RespondRequest,
  RpcError,
  RpcId,
  RpcRequest,
  RpcResult,
} from './api.ts'
import { getApiKey, notifyUnauthorized } from '../auth/apiKeyStore.ts'

// ───────────────────────── HTTP RPC 客户端 ─────────────────────────

export type RpcTransportKind = 'timeout' | 'network' | 'http' | 'bad-body'

/** 载体层失败：网络不可达 / 超时 / HTTP 非 200 / 响应体不可解析。 */
export class ApiTransportError extends Error {
  override readonly name = 'ApiTransportError'
  readonly kind: RpcTransportKind
  readonly status: number | undefined
  constructor(kind: RpcTransportKind, message: string, status?: number) {
    super(message)
    this.kind = kind
    this.status = status
  }
}

/** 业务失败：HTTP 200 + {ok:false,error}。 */
export class ApiBusinessError extends Error {
  override readonly name = 'ApiBusinessError'
  readonly method: ApiMethodName
  readonly rpcError: RpcError
  constructor(method: ApiMethodName, rpcError: RpcError) {
    super(`/api/${method} 业务错误 ${rpcError.code}: ${rpcError.message}`)
    this.method = method
    this.rpcError = rpcError
  }
}

/** 单次 RPC 默认超时（AbortSignal.timeout）。 */
export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000

export interface ApiClientOptions {
  /** 缺省取 window.location.origin（开发态经 vite proxy 到 8700）。 */
  baseUrl?: string
  requestTimeoutMs?: number
}

/** fetch POST /api/{method} 上行：rpcId 递增 + 超时 + 业务错误恒 200 信封。 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly requestTimeoutMs: number
  private nextRpc = 0

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? window.location.origin
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  }

  /** 递增 RPC 编号（会话内单调，approval 应答回响的也是这套编号）。 */
  private nextRpcId(): RpcId {
    this.nextRpc += 1
    return `rpc-${this.nextRpc}`
  }

  /** 调用一个上行方法；resolve 即业务成功，失败抛 ApiTransportError / ApiBusinessError。 */
  async call<K extends ApiMethodName>(
    method: K,
    payload: ApiMethodMap[K]['request'],
  ): Promise<ApiMethodMap[K]['response']> {
    const body: RpcRequest<ApiMethodMap[K]['request']> = {
      rpcId: this.nextRpcId(),
      method,
      payload,
    }
    let res: Response
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    const apiKey = getApiKey()
    if (apiKey !== null) headers['X-API-Key'] = apiKey
    try {
      res = await fetch(`${this.baseUrl}/api/${method}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.requestTimeoutMs),
      })
    } catch (cause) {
      const kind = cause instanceof DOMException && cause.name === 'TimeoutError' ? 'timeout' : 'network'
      throw new ApiTransportError(kind, `POST /api/${method} 载体失败：${errorMessage(cause)}`)
    }
    if (res.status === 401) {
      notifyUnauthorized()
      throw new ApiTransportError('http', `POST /api/${method} 未授权（401）`, 401)
    }
    if (!res.ok) {
      // HTTP 状态只表达载体层；业务错误永远是 200 信封
      throw new ApiTransportError('http', `POST /api/${method} 载体层错误 HTTP ${res.status}`, res.status)
    }
    let envelope: RpcResult<ApiMethodMap[K]['response']>
    try {
      envelope = (await res.json()) as RpcResult<ApiMethodMap[K]['response']>
    } catch (cause) {
      throw new ApiTransportError('bad-body', `POST /api/${method} 响应不可解析：${errorMessage(cause)}`)
    }
    if (!envelope.ok) throw new ApiBusinessError(method, envelope.error)
    return envelope.result
  }

  /** 审批应答：走 POST /api/respond，rpcId 回响 approval/requested 帧。 */
  respond(request: RespondRequest): Promise<Record<string, never>> {
    return this.call('respond', request)
  }
}

// ───────────────────────── ConnectionController ─────────────────────────

/** 重连/退避可调参数（缺省即 v4 冻结值）。 */
export interface ConnectionConfig {
  /** 首次退避基数 ms（抖动：实际延迟 = cap/2..cap）。 */
  backoffBaseMs?: number
  /** 每次连续失败的指数增长因子。 */
  backoffFactor?: number
  /** 退避上限 ms。 */
  backoffMaxMs?: number
  /** 两流 onOpen 握手等待上限 ms（超时按 dsh 语义照常 connected）。 */
  streamOpenTimeoutMs?: number
}

const CONNECTION_DEFAULTS: Required<ConnectionConfig> = {
  backoffBaseMs: 500,
  backoffFactor: 2,
  backoffMaxMs: 10_000,
  streamOpenTimeoutMs: 3_000,
}

/** 面向 UI 的粗粒度连接状态（首连前的空窗不算 outage，UI 自显 connecting）。 */
export type ConnectionState = 'connected' | 'reconnecting'

/** 帧下沉回调：controller 只管物理流，业务分派归 SessionManager（F1+ 装配）。 */
export interface ConnectionSinks {
  onMuxFrame?: (frame: MuxFrame) => void
  onHostFrame?: (frame: HostFrame) => void
  /** 每代连接严格握手成功后（含首次）。 */
  onConnected?: (description: HostDescription) => void
  /** 状态跳变去重回调。 */
  onStateChange?: (state: ConnectionState) => void
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms)
    signal.addEventListener('abort', done, { once: true })
    function done(): void {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
  })
}

/** 下行流地址（开发态经 vite ws proxy 指向后端 8700）。 */
function downlinkUrl(kind: 'mux' | 'host'): string {
  const scheme = window.location.protocol === 'https:' ? 'wss://' : 'ws://'
  const apiKey = getApiKey()
  const qs = apiKey !== null ? `?api_key=${encodeURIComponent(apiKey)}` : ''
  return `${scheme}${window.location.host}/api/events.${kind}${qs}`
}

/**
 * 双流连接 + 退避重连机器。状态（generation/attempt）是实例私有，不入 store。
 * 帧 sink 异常不拖垮泵（业务层坏了不能把连接层带崩）。
 */
export class ConnectionController {
  private readonly api: ApiClient
  private readonly sinks: ConnectionSinks
  private readonly config: Required<ConnectionConfig>
  private generation = 0
  private attempt = 0
  private running = false
  private lastState: ConnectionState | null = null
  private activeAbort: AbortController | null = null

  constructor(api: ApiClient, sinks: ConnectionSinks = {}, config: ConnectionConfig = {}) {
    this.api = api
    this.sinks = sinks
    this.config = { ...CONNECTION_DEFAULTS, ...config }
  }

  /** 幂等：开始连接/泵/重连循环。 */
  start(): void {
    if (this.running) return
    this.running = true
    void this.loop()
  }

  /** 停止循环并中止当前代际的双流。 */
  stop(): void {
    this.running = false
    this.activeAbort?.abort()
    this.activeAbort = null
  }

  private backoffDelay(attempt: number): number {
    const { backoffBaseMs, backoffFactor, backoffMaxMs } = this.config
    const cap = Math.min(backoffMaxMs, backoffBaseMs * backoffFactor ** Math.max(0, attempt - 1))
    return cap / 2 + Math.random() * (cap / 2)
  }

  private emitState(state: ConnectionState): void {
    if (this.lastState === state) return
    this.lastState = state
    try {
      this.sinks.onStateChange?.(state)
    } catch (cause) {
      console.error('[secai-connection] onStateChange sink 异常（隔离）', cause)
    }
  }

  /**
   * 泵一条下行流：onOpen 记入握手，onmessage 解析为帧并交 sink，
   * close/error 后 resolve（调用方据此收口代际）。
   */
  private pump<F extends { type: string }>(
    kind: 'mux' | 'host',
    sockets: WebSocket[],
    opened: () => void,
    onFrame: (frame: F) => void,
  ): Promise<void> {
    const socket = new WebSocket(downlinkUrl(kind))
    sockets.push(socket)
    return new Promise<void>((resolve) => {
      socket.onopen = () => {
        opened()
      }
      socket.onmessage = (event) => {
        let raw: unknown
        try {
          raw = JSON.parse(String(event.data))
        } catch {
          console.warn(`[secai-connection] ${kind} 下行帧非法 JSON，已跳过`)
          return
        }
        // 结构门后交 sink（缺 type 的垃圾帧不得进入业务层）
        if (typeof raw !== 'object' || raw === null || !('type' in raw) || typeof (raw as { type: unknown }).type !== 'string') {
          console.warn(`[secai-connection] ${kind} 下行帧缺 type，已跳过`)
          return
        }
        const frame = raw as F
        if (frame.type === 'stream/error') {
          console.warn(`[secai-connection] ${kind} stream/error：${errorMessage((raw as { message?: unknown }).message)}`)
        }
        try {
          onFrame(frame)
        } catch (cause) {
          console.error(`[secai-connection] ${kind} 帧 sink 异常（隔离，泵继续）`, cause)
        }
      }
      socket.onerror = () => {
        // 由 onclose 统一收口（WS 错误后必关）
      }
      socket.onclose = (event) => {
        if (event.code === 4401) {
          notifyUnauthorized()
        }
        resolve()
      }
    })
  }

  private async loop(): Promise<void> {
    while (this.running) {
      const gen = ++this.generation
      const ac = new AbortController()
      this.activeAbort = ac
      const sockets: WebSocket[] = []
      ac.signal.addEventListener(
        'abort',
        () => {
          for (const socket of sockets) {
            if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
              socket.close()
            }
          }
        },
        { once: true },
      )

      // 握手门闩：两流各自 onOpen 才算"物理流已建立"
      let muxOpened = (): void => {}
      let hostOpened = (): void => {}
      const streamsOpen = Promise.all([
        new Promise<void>((resolve) => {
          muxOpened = resolve
        }),
        new Promise<void>((resolve) => {
          hostOpened = resolve
        }),
      ])

      // 任一泵结束（流关闭）即视为本代失败；收口另一条流
      const failed = new Promise<void>((resolve) => {
        const settle = (): void => {
          if (gen === this.generation && !ac.signal.aborted) ac.abort()
          resolve()
        }
        void this.pump<MuxFrame>('mux', sockets, muxOpened, (frame) => this.sinks.onMuxFrame?.(frame)).then(settle)
        void this.pump<HostFrame>('host', sockets, hostOpened, (frame) => this.sinks.onHostFrame?.(frame)).then(settle)
      })

      try {
        // 严格握手：describe 证明一元可达 + 两流 onOpen（超时照常 proceed，
        // 迟到流由失败收口兜底 —— 与 dsh ConnectionController 一致）
        const timeout = new AbortController()
        const [description] = await Promise.all([
          this.api.call('describe', {}),
          Promise.race([streamsOpen, sleep(this.config.streamOpenTimeoutMs, timeout.signal)]),
        ])
        timeout.abort()
        if (ac.signal.aborted) throw new Error('代际在握手期间被中止')
        this.attempt = 0
        this.emitState('connected')
        if (gen === this.generation && !ac.signal.aborted) {
          try {
            this.sinks.onConnected?.(description)
          } catch (cause) {
            console.error('[secai-connection] onConnected sink 异常（隔离）', cause)
          }
        }
      } catch (cause) {
        if (!ac.signal.aborted) ac.abort()
        console.warn(`[secai-connection] 第 ${gen} 代握手失败：${errorMessage(cause)}`)
      }

      await failed
      if (!this.running) return
      this.emitState('reconnecting')
      this.attempt += 1
      console.warn(`[secai-connection] 连接丢失，第 ${this.attempt} 次退避重试`)
      const idle = new AbortController()
      await sleep(this.backoffDelay(this.attempt), idle.signal)
      if (!this.running) return
      // 重连 = 重开双流 + 上层 history 回填（无 since-cursor；subscribed 帧即基线）
    }
  }
}

/** 装配便捷函数：建 client + controller 并立即 start（由上层 runtime 调用）。 */
export function createConnection(
  sinks: ConnectionSinks,
  config?: ConnectionConfig,
  api?: ApiClient,
): ConnectionController {
  const controller = new ConnectionController(api ?? new ApiClient(), sinks, config)
  controller.start()
  return controller
}
