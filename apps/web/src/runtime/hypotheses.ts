/**
 * 假设队列可视化（顶级推理可解释性）：从会话快照派生「推理假设」视图。
 *
 * 数据源三处合流（均为流上真实数据，demo / 真实模式同一解析路径）：
 * - session/queue 帧（QueueItem：statement / status / priority / attempts）→ 队列主体；
 * - projection.dead_ends（DeadEndItem：reason / overturnCondition）→ 死路失败原因；
 * - projection.evidence（EvidenceChain：kind=scan/hypothesis/tool/finding 节点）
 *   与 hypothesis / anchor / dead_end / tool 事件 → 证据链与置信度信号。
 *
 * 纯投影函数（对齐 projections.ts 解析器风格）：形状不符走兜底，不抛错。
 */

import type { SessionEvent } from '../connection/api.ts'
import type { SessionSnapshot } from './session.ts'
import type { DeadEndItem, EvidenceChain } from './projections.ts'
import { parseDeadEnds, parseEvidence } from './projections.ts'

// ───────────────────────── 视图模型 ─────────────────────────

/** 假设生命周期 → 三态展示：active 验证中 / confirmed 已确认 / dead_end 死路。 */
export type HypothesisStatus = 'active' | 'confirmed' | 'dead_end'

/** 置信度档位（0-1 连续值映射为 high/med/low 标签 + 进度条宽度）。 */
export type ConfidenceBand = 'high' | 'med' | 'low'

/** 单条证据：类型 + 摘要 + 强度（对齐 projection.evidence 节点与事件解析）。 */
export interface HypothesisEvidence {
  /** 证据类型：scan / hypothesis / tool / finding / anchor / dead_end … */
  type: string
  /** 证据摘要（节点 label 或事件产出首行）。 */
  summary: string
  /** 证据强度 0-1（finding/confirmed 记强，scan 中等，排除类弱）。 */
  strength: number
}

/** 假设队列条目（HypothesisQueue 组件数据源）。 */
export interface HypothesisView {
  id: string
  /** 展示标题（queue statement，或死路 statement 兜底）。 */
  title: string
  status: HypothesisStatus
  /** 置信度 0-1（由状态 + 证据强度合成）。 */
  confidence: number
  /** 置信度档位标签。 */
  band: ConfidenceBand
  /** 队列优先级（0-10，session/queue 帧给出；死路兜底 0）。 */
  priority: number
  /** 已尝试次数（session/queue 帧给出）。 */
  attempts: number
  /** 后端 hypothesisId（hyp-a1 ↔ hyp-a1 的事件归并键；死路匹配用）。 */
  hypothesisKey?: string
  /** 证据链（按时间序；空数组 = 无关联证据）。 */
  evidence: HypothesisEvidence[]
  /** 死路：失败原因（projection.dead_ends.reason）。 */
  deadReason?: string
  /** 死路：复活条件（overturn_condition）。 */
  overturnCondition?: string
}

// ───────────────────────── 容错解析工具 ─────────────────────────

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

/** 置信度 0-1 → 档位标签。 */
function bandOf(confidence: number): ConfidenceBand {
  if (confidence >= 0.67) return 'high'
  if (confidence >= 0.34) return 'med'
  return 'low'
}

/** 证据强度 0-1 → 档位（导出供组件标签复用）。 */
export function strengthBand(strength: number): ConfidenceBand {
  return bandOf(strength)
}

// ───────────────────────── 事件证据提取 ─────────────────────────

/**
 * 从单条会话事件提取证据（hypothesis/anchor/dead_end 结构化事件优先，
 * tool/output 摘要兜底）。返回 null = 该事件与本假设无关。
 */
function evidenceFromEvent(event: SessionEvent): HypothesisEvidence | null {
  const data = asRecord(event.data) ?? {}
  const firstLine = (text: string): string => text.split('\n')[0] ?? ''
  switch (event.type) {
    case 'hypothesis': {
      // 结构化假设事件：statement + confidence（真实模式后端 hypothesis 事件）
      return {
        type: 'hypothesis',
        summary: str(data.statement, str(data.title, '提出假设')),
        strength: clamp01(num(data.confidence, 0.4)),
      }
    }
    case 'anchor': {
      // 锚点事件：侦察锚定（端口/服务/路径），支撑假设成立的观察
      return {
        type: 'anchor',
        summary: str(data.summary, str(data.label, firstLine(str(data.output, '锚点观察')))),
        strength: clamp01(num(data.strength, 0.55)),
      }
    }
    case 'dead_end':
    case 'deadend': {
      // 死路事件：证伪/排除观察（弱强度——它是反向证据，只用于置信度折减）
      return {
        type: 'dead_end',
        summary: str(data.reason, str(data.statement, '路径排除')),
        strength: 0.2,
      }
    }
    case 'tool/output': {
      // 工具产出兜底证据（排除类输出降权）
      const output = str(data.output, str(data.result, ''))
      if (output === '') return null
      const excluded = /排除|不匹配|失败|denied|refused/i.test(firstLine(output))
      return {
        type: 'tool',
        summary: firstLine(output).slice(0, 80),
        strength: excluded ? 0.3 : 0.6,
      }
    }
    default:
      return null
  }
}

// ───────────────────────── 证据链聚合 ─────────────────────────

/** projection.evidence 链节点 → 证据条目（kind 直接作类型，strength 按 kind 定档）。 */
function evidenceFromChains(hypothesisId: string, chains: readonly EvidenceChain[]): HypothesisEvidence[] {
  const collected: HypothesisEvidence[] = []
  for (const chain of chains) {
    for (const node of chain.nodes) {
      // 节点 detail 提及本假设 id（如 demo 帧 n2「假设记录并进入验证」detail 含 /actuator）
      // 或 kind=hypothesis 的节点属于公共链路——宽松归并，摘要带来源链标题
      const mentions = node.detail?.includes(hypothesisId) ?? false
      const kind = node.kind ?? 'tool'
      if (!mentions && kind !== 'hypothesis' && kind !== 'finding' && kind !== 'scan') continue
      const strength = kind === 'finding' ? 0.95 : kind === 'hypothesis' ? 0.5 : kind === 'scan' ? 0.45 : 0.6
      collected.push({
        type: kind,
        summary: node.detail !== undefined && node.detail !== '' ? `${node.label} —— ${node.detail}` : node.label,
        strength,
      })
    }
  }
  return collected
}

// ───────────────────────── 置信度合成 ─────────────────────────

/**
 * 置信度合成（0-1）：
 * - 基准 = 状态分位（validated→0.9 / testing→0.6 / pending→0.45 / falsified→0.1）；
 * - 尝试越多未证伪 → 小幅上调（persistence bonus，封顶 +0.1）；
 * - 证据均值偏移（强铁证上调、排除类下调）。
 */
function composeConfidence(status: string, attempts: number, evidences: readonly HypothesisEvidence[]): number {
  const baseByStatus: Record<string, number> = {
    pending: 0.45,
    testing: 0.6,
    validated: 0.9,
    falsified: 0.1,
    inconclusive: 0.35,
  }
  let confidence = baseByStatus[status] ?? 0.45
  // 尝试 persistence bonus：最多 +0.1（尝试 3 次封顶）
  confidence += Math.min(attempts, 3) * 0.033
  // 证据偏移：均值与 0.5 的差折半并入（铁证 0.95 → +0.22；排除 0.2 → -0.15）
  if (evidences.length > 0) {
    const mean = evidences.reduce((sum, ev) => sum + ev.strength, 0) / evidences.length
    confidence += (mean - 0.5) * 0.45
  }
  return clamp01(confidence)
}

// ───────────────────────── 主派生函数 ─────────────────────────

/**
 * 从会话快照派生假设队列视图（DetailsView「推理假设」区块数据源）。
 *
 * 归并规则：
 * - session/queue 每条 → 一个条目；projection.dead_ends 按 hypothesisId 并入
 *   （status → dead_end，附失败原因/复活条件）；未被 queue 覆盖的死路独立成行；
 * - status 映射：pending/testing → active；validated → confirmed；
 *   falsified 与 dead_ends → dead_end；
 * - 证据 = 事件窗口（hypothesis/anchor/dead_end/tool/output）+ evidence 链节点；
 * - 排序：confirmed > active > dead_end，同档按 priority 降序。
 */
export function deriveHypotheses(snapshot: SessionSnapshot): HypothesisView[] {
  // demo 帧的 dead_ends 投影直发裸数组（fixture/demo.ts 同一口径，无 {items} 包装）；
  // parseDeadEnds 期望 {items: [...]}——两者都兼容：裸数组直接映射，包装走解析器。
  const rawDeadEnds = snapshot.projections.dead_ends
  const deadEnds: DeadEndItem[] = Array.isArray(rawDeadEnds)
    ? (rawDeadEnds as unknown[]).map((raw, index) => {
        const item = asRecord(raw) ?? {}
        return {
          id: str(item.id, `dead-end-${index}`),
          hypothesisId: item.hypothesisId !== undefined ? str(item.hypothesisId) : undefined,
          statement: str(item.statement, str(item.hypothesis, '未命名假设')),
          reason: item.reason !== undefined ? str(item.reason) : undefined,
          overturnCondition: str(item.overturnCondition, str(item.overturn_condition, '')),
          decidedAt: str(item.decidedAt, ''),
        }
      })
    : (parseDeadEnds(rawDeadEnds) ?? [])
  const chains = parseEvidence(snapshot.projections.evidence) ?? []
  const views = new Map<string, HypothesisView>()

  // 1) session/queue 帧 → 队列主体
  for (const item of snapshot.queue) {
    const rawStatus = item.status
    const status: HypothesisStatus = rawStatus === 'validated'
      ? 'confirmed'
      : (rawStatus === 'falsified' ? 'dead_end' : 'active')
    const dead = deadEnds.find((end) => end.hypothesisId !== undefined && end.hypothesisId === item.hypothesisId)
    const evidence = evidenceFromChains(item.hypothesisId ?? item.id, chains)
    const confidence = dead !== undefined && status !== 'confirmed'
      ? 0.12
      : composeConfidence(rawStatus, item.attempts, evidence)
    views.set(item.id, {
      id: item.id,
      title: item.statement,
      status,
      confidence,
      band: bandOf(confidence),
      priority: item.priority,
      attempts: item.attempts,
      ...(item.hypothesisId !== undefined ? { hypothesisKey: item.hypothesisId } : {}),
      evidence,
      ...(dead !== undefined ? { deadReason: dead.reason, overturnCondition: dead.overturnCondition } : {}),
    })
  }

  // 2) 未被 queue 覆盖的死路 → 独立成行（死路归档区）
  for (const dead of deadEnds) {
    if (dead.hypothesisId !== undefined && [...views.values()].some((view) =>
      view.title === dead.statement || dead.hypothesisId === `hyp-${view.id.replace(/^h-/, '')}`,
    )) {
      continue
    }
    const evidence: HypothesisEvidence[] = dead.reason !== undefined && dead.reason !== ''
      ? [{ type: 'dead_end', summary: dead.reason, strength: 0.2 }]
      : []
    views.set(dead.id, {
      id: dead.id,
      title: dead.statement,
      status: 'dead_end',
      confidence: 0.12,
      band: 'low',
      priority: 0,
      attempts: 0,
      evidence,
      deadReason: dead.reason,
      overturnCondition: dead.overturnCondition,
    })
  }

  // 3) 事件窗口兜底：队列与死路均未覆盖的 hypothesis/anchor 事件独立成行
  for (const event of snapshot.events) {
    if (event.type !== 'hypothesis' && event.type !== 'anchor') continue
    const data = asRecord(event.data) ?? {}
    const eventHypId = str(data.hypothesisId, str(data.id, ''))
    const covered = eventHypId !== '' && [...views.values()].some((view) =>
      view.id === eventHypId || `hyp-${view.id.replace(/^h-/, '')}` === eventHypId,
    )
    if (covered) continue
    const ev = evidenceFromEvent(event)
    if (ev === null) continue
    const confidence = clamp01(num(data.confidence, ev.strength))
    views.set(`ev-${event.eventId}`, {
      id: `ev-${event.eventId}`,
      title: str(data.statement, str(data.summary, '流上假设')),
      status: 'active',
      confidence,
      band: bandOf(confidence),
      priority: num(data.priority, 3),
      attempts: 0,
      evidence: [ev],
    })
  }

  // 4) 把事件证据宽松归并到同会话各假设（按 id 提及匹配；不匹配则跳过，
  //    避免把无关工具输出铺满每条假设）
  for (const event of snapshot.events) {
    const ev = evidenceFromEvent(event)
    if (ev === null || event.type === 'hypothesis' || event.type === 'anchor') continue
    for (const view of views.values()) {
      const related = ev.summary.includes(view.id)
        || ev.summary.includes(view.title.slice(0, 12))
        || (view.hypothesisKey !== undefined && ev.summary.includes(view.hypothesisKey))
      if (related && !view.evidence.some((existing) => existing.summary === ev.summary)) {
        view.evidence.push(ev)
      }
    }
  }

  // 5) 排序：状态档（confirmed > active > dead_end），档内 priority 降序
  const statusRank: Record<HypothesisStatus, number> = { confirmed: 0, active: 1, dead_end: 2 }
  return [...views.values()].sort((a, b) =>
    (statusRank[a.status] - statusRank[b.status]) || (b.priority - a.priority),
  )
}

/** 供组件展示的死路/确认附加说明（deadReason 已在视图内，此处仅语义标签）。 */
export const HYPOTHESIS_STATUS_LABEL: Record<HypothesisStatus, string> = {
  active: '验证中',
  confirmed: '已确认',
  dead_end: '死路',
}

/** 置信度档位中文标签。 */
export const CONFIDENCE_LABEL: Record<ConfidenceBand, string> = {
  high: '高',
  med: '中',
  low: '低',
}

/** 导出类型再导出（DeadEndItem 供 DetailsView 死路计数等复用）。 */
export type { DeadEndItem }
