/**
 * F2/F3 DEMO 驱动（无后端预览）：内嵌一串 mock MuxFrame/HostFrame，经对象
 * 层（Engagement/Session 的 apply 方法）真实落账——不伪造网络、不发 HTTP、
 * 不开 WS。帧内容与类型约定严格对齐 connection/api.ts 契约与后端 ws.py
 * 口径（R3/R4 合流后同一序列可由真实后端替代）。
 *
 * 场景：授权任务书 → 三目标会话并行。
 * - sess-a-demo：10.10.5.2 · demo.ine.local（running，interval 持续活动）
 * - sess-b-demo：10.10.5.0/24 · 横向发现（awaiting_approval → 等待人工审批
 *   hydra 口令探测；respond 后放行推进）
 * - sess-c-demo：vulnapp.example（收尾转 completed：证据链 + 报告 + 成长全量）
 *
 * 交错投递：三会话帧按 90ms 步进轮流注入，模拟多目标并行直播；同一会话
 * 内保持 FIFO，事件 seq 单调。
 */

import type {
  ApprovalRequest,
  ApprovalResolution,
  HostFrame,
  MuxFrame,
  QueueItem,
  SessionHeader,
} from '../connection/api.ts'

/** DEMO 场景的 engagement id（AppRuntime 中 Engagement 同一 id）。 */
export const DEMO_ENGAGEMENT_ID = 'eng-demo'

export interface DemoRuntimeHost {
  applyHostFrame(frame: HostFrame): void
  applyMuxFrame(frame: MuxFrame): void
}

export interface DemoController {
  start(): void
  stop(): void
  /** 审批应答（ApprovalCard → demo 本地裁决并广播 resolution + 状态推进）。 */
  respond(sessionId: string, rpcId: string, decision: 'allow' | 'deny', comment?: string): void
  /** 指令发送（InputBar → 追加 user 轮次 + 延迟 assistant 回复）。 */
  send(sessionId: string, text: string): void
}

const A = 'sess-a-demo'
const B = 'sess-b-demo'
const C = 'sess-c-demo'

function iso(offsetMs = 0): string {
  return new Date(Date.now() - offsetMs).toISOString()
}

function header(sessionId: string, target: string, status: SessionHeader['status']): SessionHeader {
  return { sessionId, target, status, engagementId: DEMO_ENGAGEMENT_ID, createdAt: iso(120_000), updatedAt: iso(2_000) }
}

/** 假设队列内容（session/queue 帧）。 */
const queueA: QueueItem[] = [
  { id: 'h-a1', hypothesisId: 'hyp-a1', statement: 'Web 应用运行带已知 CVE 的框架版本', status: 'testing', priority: 9, attempts: 2 },
  { id: 'h-a2', hypothesisId: 'hyp-a2', statement: '8000/Jetty 默认上下文存在未授权端点', status: 'pending', priority: 7, attempts: 0 },
  { id: 'h-a3', hypothesisId: 'hyp-a3', statement: '管理子域接口存在弱口令', status: 'pending', priority: 5, attempts: 0 },
]

const queueB: QueueItem[] = [
  { id: 'h-b1', hypothesisId: 'hyp-b1', statement: '存活主机存在 SSH 弱口令（字典 top-500）', status: 'pending', priority: 8, attempts: 1 },
]

const queueC: QueueItem[] = [
  { id: 'h-c1', hypothesisId: 'hyp-c1', statement: 'Actuator 未授权暴露内部端点', status: 'validated', priority: 9, attempts: 3 },
  { id: 'h-c2', hypothesisId: 'hyp-c2', statement: 'Tomcat 管理口存在弱口令', status: 'falsified', priority: 6, attempts: 2 },
]

/** 单会话帧队列：同一会话 FIFO，seq/projection-seq 单调。
 * compose 期用 event()/queue()/… 排队待交错投递；运行期（interval / 用户
 * 动作）用 live() 即时经 sink 输出。seq 计数两者共享，单调不重。 */
class SessionFeed {
  private seq = 0
  private projSeq = -1
  private readonly pending: Array<{ make: () => MuxFrame }> = []
  private readonly sessionId: string
  private readonly sink: (frame: MuxFrame) => void

  constructor(sessionId: string, sink: (frame: MuxFrame) => void) {
    this.sessionId = sessionId
    this.sink = sink
  }

  event(type: string, data: Record<string, unknown>): void {
    const seq = ++this.seq
    this.pending.push({
      make: () => ({
        type: 'session/event',
        sessionId: this.sessionId,
        data: { eventId: `evt-${this.sessionId}-${seq}`, seq, type, data, createdAt: iso() },
      }),
    })
  }

  /** 运行期即时投递（不再排队）。 */
  live(type: string, data: Record<string, unknown>): void {
    const seq = ++this.seq
    this.sink({
      type: 'session/event',
      sessionId: this.sessionId,
      data: { eventId: `evt-${this.sessionId}-${seq}`, seq, type, data, createdAt: iso() },
    })
  }

  queue(items: QueueItem[]): void {
    this.pending.push({ make: () => ({ type: 'session/queue', sessionId: this.sessionId, items }) })
  }

  projection(values: Record<string, unknown>): void {
    const seq = ++this.projSeq
    this.pending.push({
      make: () => ({ type: 'session/projection', sessionId: this.sessionId, values, seq }),
    })
  }

  approval(rpcId: string, payload: ApprovalRequest): void {
    this.pending.push({
      make: () => ({ type: 'approval/requested', sessionId: this.sessionId, rpcId, payload }),
    })
  }

  subscribed(): void {
    this.pending.push({
      make: () => ({ type: 'session/subscribed', sessionId: this.sessionId, lastSeq: this.seq }),
    })
  }

  hasNext(): boolean {
    return this.pending.length > 0
  }

  next(): MuxFrame {
    const entry = this.pending.shift()
    if (entry === undefined) throw new Error('feed drained')
    return entry.make()
  }
}

class DemoDriver implements DemoController {
  private timers = new Set<ReturnType<typeof setTimeout>>()
  private loop: ReturnType<typeof setInterval> | null = null
  private aTick = 0
  private readonly host: DemoRuntimeHost
  /** 可继续接收用户动作的会话 feed（running 会话）。 */
  private readonly actives = new Map<string, SessionFeed>()

  constructor(host: DemoRuntimeHost) {
    this.host = host
  }

  private later(ms: number, fn: () => void): void {
    const timer = setTimeout(() => {
      this.timers.delete(timer)
      try {
        fn()
      } catch (cause) {
        console.error('[secai-demo] 帧投递异常（隔离）', cause)
      }
    }, ms)
    this.timers.add(timer)
  }

  start(): void {
    this.pumpInitial()
    this.loop = setInterval(() => this.pumpActivity(), 5_600)
    this.timers.add(this.loop)
  }

  stop(): void {
    for (const timer of this.timers) clearTimeout(timer)
    this.timers.clear()
    this.loop = null
  }

  /** 初始序列：host 三行登记 → 交错注入 mux 帧 → C 会话收尾置 completed。 */
  private pumpInitial(): void {
    const hostFrames: HostFrame[] = [
      { type: 'host/session-added', session: header(A, '10.10.5.2 · demo.ine.local', 'running') },
      { type: 'host/session-added', session: header(B, '10.10.5.0/24 · 横向发现', 'awaiting_approval') },
      { type: 'host/session-added', session: header(C, 'vulnapp.example', 'running') },
    ]
    hostFrames.forEach((frame, index) => this.later(60 * index + 40, () => this.host.applyHostFrame(frame)))

    const feedA = new SessionFeed(A, (frame) => this.host.applyMuxFrame(frame))
    const feedB = new SessionFeed(B, (frame) => this.host.applyMuxFrame(frame))
    const feedC = new SessionFeed(C, (frame) => this.host.applyMuxFrame(frame))
    this.composeA(feedA)
    this.composeB(feedB)
    this.composeC(feedC)
    this.actives.set(A, feedA)
    this.actives.set(B, feedB)

    // 交错投递：轮流取队首，步进 90ms
    const feeds = [feedA, feedB, feedC]
    let cursor = 240
    let remaining = true
    while (remaining) {
      remaining = false
      for (const feed of feeds) {
        if (!feed.hasNext()) continue
        remaining = true
        const frame = feed.next()
        const at = cursor
        cursor += 90
        this.later(at, () => this.host.applyMuxFrame(frame))
      }
    }
    // C 会话收尾：mux 流稳定后 host 置 completed
    this.later(cursor + 500, () => this.host.applyHostFrame({ type: 'host/session-status', sessionId: C, status: 'completed' }))
  }

  private composeA(feed: SessionFeed): void {
    feed.subscribed()
    feed.queue(queueA)
    feed.event('message', { role: 'assistant', content: '任务已接受：授权目标 demo.ine.local（10.10.5.2），约束 = 先被动再主动、禁 DoS、时间窗 60 分钟。计划：服务发现 → 指纹 → 已知 CVE 验证，全程留存证据。' })
    feed.event('subagent', { name: 'port-scanner', role: '子任务：端口/服务枚举', state: 'running' })
    feed.event('subagent', { name: 'banner-grab', role: '子任务：横幅/指纹采集', state: 'running' })
    feed.event('tool/call', { tool: 'nmap-scan', args: '-sV -p- --open 10.10.5.2' })
    feed.event('tool/output', {
      tool: 'nmap-scan', ok: true,
      output: 'PORT     STATE SERVICE  VERSION\n22/tcp   open  ssh      OpenSSH 8.9p1 Ubuntu\n80/tcp   open  http     nginx 1.24.0\n443/tcp  open  https    nginx 1.24.0\n8000/tcp open  http     Jetty 11.0.15',
    })
    feed.event('message', { role: 'assistant', content: '服务面收敛：22/80/443/8000。80 与 443 同源 nginx，8000 是独立的 Jetty —— 优先指纹 Web 应用与 Jetty 上下文。' })
    feed.event('subagent', { name: 'banner-grab', role: '子任务：横幅/指纹采集', state: 'done' })
    feed.event('tool/call', { tool: 'http-probe', args: 'GET https://demo.ine.local/' })
    feed.event('tool/output', {
      tool: 'http-probe', ok: true,
      output: 'HTTP/1.1 302 Found\nLocation: /login?next=/\nServer: nginx\nSet-Cookie: session=…; HttpOnly; SameSite=Lax\n\n→ 应用根路径跳登录，无版本泄露；下一步枚举静态资源与常见框架路径。',
    })
    feed.event('message', { role: 'assistant', content: '跳转 /login，Cookie 属性健康。假设：前端框架或网关存在已知 CVE；继续抓 8000/Jetty 指纹。' })
    feed.event('subagent', { name: 'port-scanner', role: '子任务：端口/服务枚举', state: 'done' })
    feed.event('subagent', { name: 'cve-matcher', role: '子任务：CVE 特征比对', state: 'running' })
    feed.projection({
      attack_surface: {
        targets: ['demo.ine.local'],
        ports: [
          { port: 22, protocol: 'tcp', service: 'ssh', version: 'OpenSSH 8.9p1', state: 'open' },
          { port: 80, protocol: 'tcp', service: 'http', version: 'nginx 1.24.0', state: 'open' },
          { port: 443, protocol: 'tcp', service: 'https', version: 'nginx 1.24.0', state: 'open' },
          { port: 8000, protocol: 'tcp', service: 'http', version: 'Jetty 11.0.15', state: 'open' },
        ],
      },
    })
    feed.projection({
      dead_ends: [
        {
          id: 'de-a1', hypothesisId: 'hyp-a0',
          statement: 'LDAP 389 匿名绑定可枚举用户',
          reason: '389 未开放，636 要求客户端证书',
          overturnCondition: '开放 389/636 且允许匿名查询时复活',
          decidedAt: iso(600_000),
        },
      ],
    })
  }

  private composeB(feed: SessionFeed): void {
    feed.subscribed()
    feed.queue(queueB)
    feed.projection({
      attack_surface: {
        targets: ['10.10.5.0/24 · 8 台存活'],
        ports: [
          { port: 22, protocol: 'tcp', service: 'ssh', state: 'open', note: '8/8 存活主机' },
          { port: 445, protocol: 'tcp', service: 'smb', state: 'open', note: '3 台' },
        ],
      },
    })
    feed.event('message', { role: 'assistant', content: '网段发现完成：10.10.5.0/24 内 8 台存活（.7/.12/.19/.23/.31/.44/.52/.60），22 全开、3 台有 445。' })
    feed.event('subagent', { name: 'lateral-sweep', role: '子任务：横向口令/服务探测', state: 'waiting' })
    feed.event('tool/call', { tool: 'ping-sweep', args: '-sn 10.10.5.0/24' })
    feed.event('tool/output', {
      tool: 'ping-sweep', ok: true,
      output: 'Nmap scan report … 8 hosts up\n10.10.5.7  10.10.5.12  10.10.5.19  10.10.5.23\n10.10.5.31  10.10.5.44  10.10.5.52  10.10.5.60',
    })
    feed.event('message', { role: 'assistant', content: '口令探测（hydra，22 端口 top-500 字典）超出 passive 边界，已发起人工审批——批准后以单主机 5/s 限速执行。' })
    feed.approval('rpc-101', {
      action: '执行登录口令探测（hydra）',
      description: '对 10.10.5.0/24 存活主机的 22 端口执行弱口令字典探测（top-500，单主机限速 5/s，超时 10 分钟）。该动作超出任务书 passive 强度，需人工放行。',
      detail: { tool: 'hydra', hosts: 8, limit: '5/s', timeout: '10min', scope: '10.10.5.0/24' },
      createdAt: iso(),
    })
  }

  private composeC(feed: SessionFeed): void {
    feed.subscribed()
    feed.queue(queueC)
    feed.projection({
      attack_surface: {
        targets: ['vulnapp.example'],
        ports: [
          { port: 8080, protocol: 'tcp', service: 'http', version: 'Tomcat 9.0.78', state: 'open' },
          { port: 8443, protocol: 'tcp', service: 'https', version: 'Tomcat 9.0.78', state: 'open' },
        ],
      },
    })
    feed.projection({
      dead_ends: [
        {
          id: 'de-c1', hypothesisId: 'hyp-c2',
          statement: 'Tomcat 管理口存在弱口令',
          reason: 'manager 路径全部 403，未开放 manager/html',
          overturnCondition: '暴露 /manager 且存在默认凭据时复活',
          decidedAt: iso(240_000),
        },
      ],
    })
    feed.event('message', { role: 'assistant', content: '授权目标 vulnapp.example：先做端口与应用面侦察，命中假设后逐条验证并留证。' })
    feed.event('subagent', { name: 'web-enum', role: '子任务：Web 应用枚举', state: 'running' })
    feed.event('tool/call', { tool: 'port-scan', args: '-sV 8443,8080 vulnapp.example' })
    feed.event('tool/output', {
      tool: 'port-scan', ok: true,
      output: '8080/tcp open http Tomcat 9.0.78\n8443/tcp open https Tomcat 9.0.78\n→ /actuator 端点返回 200（无鉴权）',
    })
    feed.event('message', { role: 'assistant', content: '发现 /actuator 未授权可达——进入假设验证：枚举敏感子端点（env/heapdump/mappings）。' })
    feed.event('tool/call', { tool: 'curl', args: 'GET http://vulnapp.example:8080/actuator/env' })
    feed.event('tool/output', {
      tool: 'curl', ok: true,
      output: 'HTTP/1.1 200 OK\n{\n  "propertySources": [ "systemProperties", "applicationConfig" ],\n  "env": { "spring.datasource.url": "jdbc:postgresql://…", "spring.datasource.username": "**REDACTED**" }\n}\n\n→ 配置键泄露（值已脱敏），确认未授权访问成立。',
    })
    feed.event('subagent', { name: 'web-enum', role: '子任务：Web 应用枚举', state: 'done' })
    feed.event('subagent', { name: 'cve-matcher', role: '子任务：CVE 特征比对', state: 'running' })
    feed.event('tool/call', { tool: 'cve-verify', args: 'Tomcat 9.0.78 / CVE-2023-42793' })
    feed.event('tool/output', {
      tool: 'cve-verify', ok: true,
      output: 'CVE-2023-42793 特征匹配失败（无 /manager 且无 .war 上传面）→ 排除；Actuator 未授权独立成证。',
    })
    feed.event('message', { role: 'assistant', content: '结论收敛：确认 1 条高价值 finding（Actuator 未授权），1 条中危（管理端口带版本暴露）。已生成结构化报告投影。' })
    feed.event('message', { role: 'assistant', content: '任务结束：负面结论一并归档（管理口弱口令、SMB 匿名共享均已证伪），供 L6 学习层沉淀。' })
    feed.projection({
      evidence: {
        chains: [
          {
            findingId: 'f-c1',
            title: 'Spring Boot Actuator 未授权访问（/actuator/env 泄露配置键）',
            severity: 'high',
            nodes: [
              { id: 'n1', at: iso(480_000), label: '端口扫描发现 8080/http', kind: 'scan', detail: 'Tomcat 9.0.78 上的 Spring Boot 应用' },
              { id: 'n2', at: iso(360_000), label: '假设记录并进入验证', kind: 'hypothesis', detail: '/actuator 200 无鉴权 → 进入 env 验证' },
              { id: 'n3', at: iso(300_000), label: 'GET /actuator/env 200', kind: 'tool', detail: '返回 propertySources + 配置键（值脱敏）' },
              { id: 'n4', at: iso(240_000), label: '确认未授权访问', kind: 'finding', detail: '写入 finding，关联配置泄露面' },
            ],
          },
          {
            findingId: 'f-c2',
            title: '8443/8080 暴露 Tomcat 版本（信息泄露）',
            severity: 'medium',
            nodes: [
              { id: 'n1', at: iso(450_000), label: 'banner 采集', kind: 'scan', detail: 'Server: Apache-Coyote/1.1 + 错误页版本戳' },
              { id: 'n2', at: iso(300_000), label: '版本矩阵比对', kind: 'verify', detail: 'CVE-2023-42793 特征不匹配，降级为信息泄露' },
            ],
          },
        ],
      },
    })
    feed.projection({
      report: {
        status: 'ready',
        summary: 'vulnapp.example 授权测试完成：确认 1 条高危（Actuator 未授权）、1 条中危（版本信息泄露）与 1 条低危（无 CSP 响应头）；2 条负面结论已归档。',
        sections: [
          { id: 's1', title: '执行摘要', body: '目标 8080/8443 由 Tomcat 9.0.78 承载 Spring Boot 应用。/actuator 未授权可达并泄露配置键（数据库地址/用户），未做进一步利用（越权边界内终止）。' },
          { id: 's2', title: '攻击面概览', body: '8443/https（主入口）、8080/http（actuator）。均暴露 Tomcat 9.0.78 版本信息。' },
          { id: 's3', title: '已排除攻击面（负面结论）', body: '1) Tomcat 管理口弱口令——manager 未开放，排除。2) SMB 匿名共享（网段内）——无共享可列，排除。' },
          { id: 's4', title: '加固建议', body: '1) Actuator 移入内网或加鉴权并关闭 env/heapdump；2) 收敛版本指纹（自定义错误页/Server 头）；3) 补 CSP 与安全响应头。' },
        ],
        findings: [
          { id: 'f-c1', title: 'Spring Boot Actuator 未授权访问', severity: 'high', summary: '/actuator/env 未鉴权返回配置键，泄露数据库地址与用户名。', evidence: ['GET /actuator/env → 200', 'propertySources 含 systemProperties/applicationConfig'] },
          { id: 'f-c2', title: 'Tomcat 版本信息泄露', severity: 'medium', summary: '8443/8080 的 Server 头与错误页暴露 9.0.78。', evidence: ['Server: Apache-Coyote/1.1', '错误页版本戳'] },
          { id: 'f-c3', title: '缺少 CSP 等安全响应头', severity: 'low', summary: '登录页响应未带 Content-Security-Policy。', evidence: ['curl -I 响应头核查'] },
        ],
        generatedAt: iso(120_000),
      },
    })
    feed.projection({
      growth: {
        metrics: { hitRate: 0.67, tokenEfficiency: 0.82, playbookReuse: 0.5 },
        series: [
          { label: 'R1', hitRate: 0.4, tokenEfficiency: 0.55, playbookReuse: 0.2 },
          { label: 'R2', hitRate: 0.5, tokenEfficiency: 0.61, playbookReuse: 0.3 },
          { label: 'R3', hitRate: 0.6, tokenEfficiency: 0.68, playbookReuse: 0.35 },
          { label: 'R4', hitRate: 0.62, tokenEfficiency: 0.74, playbookReuse: 0.45 },
          { label: 'R5', hitRate: 0.67, tokenEfficiency: 0.82, playbookReuse: 0.5 },
        ],
      },
    })
  }

  /** 会话 A 的 interval 活动（模拟侦察 agent 持续产出）。 */
  private pumpActivity(): void {
    const feed = this.actives.get(A)
    if (feed === undefined) return
    this.aTick += 1
    const variant = this.aTick % 3
    feed.event('subagent', { name: 'cve-matcher', role: '子任务：CVE 特征比对', state: 'running' })
    if (variant === 0) {
      feed.event('message', { role: 'assistant', content: '后台比对 8000/Jetty 与 nginx 特征库…暂无新命中，维持假设 h-a1 验证中。' })
      feed.event('tool/output', { tool: 'cve-lookup', ok: true, output: 'CVE-2021-34429 (Jetty) 需共享缓存前缀 → 条件不满足，排除。' })
    } else if (variant === 1) {
      feed.event('tool/output', { tool: 'http-probe', ok: true, output: 'GET /login 200 · 页面引用 /static/app.js（前端框架版本待提取）。' })
    } else {
      feed.event('message', { role: 'assistant', content: '指纹库覆盖正常；继续对 h-a2（Jetty 默认上下文）做路径枚举。' })
    }
    feed.event('subagent', { name: 'cve-matcher', role: '子任务：CVE 特征比对', state: 'done' })
  }

  // ───────────── 用户动作（InputBar / ApprovalCard）─────────────

  respond(sessionId: string, rpcId: string, decision: 'allow' | 'deny', comment?: string): void {
    if (sessionId !== B) return
    const feed = this.actives.get(B)
    if (feed === undefined) return
    const resolution: ApprovalResolution = { decision, ...(comment !== undefined ? { comment } : {}), decidedAt: iso() }
    this.later(80, () => this.host.applyMuxFrame({ type: 'approval/resolved', sessionId, rpcId, payload: resolution }))
    this.later(240, () => this.host.applyHostFrame({ type: 'host/session-status', sessionId: B, status: 'running' }))
    if (decision === 'allow') {
      this.later(600, () => feed.live('message', { role: 'assistant', content: '审批已放行——hydra 以单主机 5/s 限速开始（top-500，10 分钟超时）。' }))
      this.later(1_600, () => feed.live('tool/call', { tool: 'hydra', args: '-L users.txt -P top500.txt ssh://10.10.5.0/24' }))
      this.later(2_800, () => feed.live('tool/output', {
        tool: 'hydra', ok: true,
        output: '10.10.5.12 ssh: login: backup password: Backup#2024 [1/2]\n10.10.5.23 ssh: login: admin password: ChangeMe123 [2/2]\n\n→ 2 组弱口令命中，等待人工确认后进入验证阶段。',
      }))
      this.later(3_800, () => feed.live('message', { role: 'assistant', content: '弱口令命中 2 组（.12/.23），已留证据。按任务书约定将弱口令验证列入下阶段——如目标网段属授权范围则转入，否则终止。' }))
    } else {
      this.later(600, () => feed.live('message', { role: 'assistant', content: '口令探测被拒——改走服务版本 → 已知 CVE 的免审批路径。' }))
      this.later(1_600, () => feed.live('tool/output', { tool: 'cve-verify', ok: true, output: 'OpenSSH 8.9p1 → CVE-2023-38408 需特定依赖，非默认；标记待人工复核。' }))
      this.later(2_600, () => feed.live('message', { role: 'assistant', content: '已按审批意见收敛为低风险验证并继续。' }))
    }
  }

  send(sessionId: string, text: string): void {
    const feed = this.actives.get(sessionId)
    if (feed === undefined) return
    feed.event('message', { role: 'user', content: text })
    const replies = [
      `收到指令「${text}」。已纳入当前验证队列，完成后回报证据与结论。`,
      `收到「${text}」——该路径在授权范围内，转入验证并留证。`,
      `理解：「${text}」。涉及的动作级别未超出任务书约束，开始执行。`,
    ]
    const reply = replies[Math.floor(Math.random() * replies.length)]!
    this.later(1_200, () => feed.event('message', { role: 'assistant', content: reply }))
  }
}

/** 创建 demo 控制器（宿主 = 对象层路由，见 AppRuntime）。 */
export function createDemo(host: DemoRuntimeHost): DemoController {
  return new DemoDriver(host)
}
