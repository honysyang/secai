/**
 * F3 投影形状 + 容错解析（R4 攻击面 / R5 报告 / R6 成长 + 死路、证据）。
 *
 * projection.* 键由后端投影写入，值在链上以 unknown 传递（见 Session
 * projectionsValue）；各详情组件从这里取强类型视图。解析器纯函数：
 * 形状不符返回 null（组件自显空态），后端合流前由 demo 帧先行对齐。
 */

/** 投影键集中登记（新增投影在此扩展）。 */
export const PROJECTION_KEYS = {
  deadEnds: 'dead_ends',
  attackSurface: 'attack_surface',
  report: 'report',
  growth: 'growth',
  evidence: 'evidence',
} as const

export interface DeadEndItem {
  id: string
  hypothesisId?: string
  statement: string
  reason?: string
  /** 复活条件：满足即从死路队列回收的触发器（overturn_condition）。 */
  overturnCondition: string
  decidedAt: string
}

export interface PortRow {
  port: number
  protocol?: string
  service?: string
  version?: string
  state?: string
  note?: string
}

export interface AttackSurfaceProjection {
  targets?: string[]
  ports: PortRow[]
  services?: string[]
}

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export interface ReportFinding {
  id: string
  title: string
  severity: Severity
  summary?: string
  evidence?: string[]
}

export interface ReportSection {
  id: string
  title: string
  body?: string
}

export interface ReportProjection {
  status: 'drafting' | 'ready'
  summary?: string
  sections?: ReportSection[]
  findings?: ReportFinding[]
  generatedAt?: string
}

export interface EvidenceNode {
  id?: string
  at: string
  label: string
  detail?: string
  kind?: string
}

export interface EvidenceChain {
  findingId: string
  title: string
  severity: Severity
  nodes: EvidenceNode[]
}

export interface GrowthSeriesPoint {
  label: string
  hitRate: number
  tokenEfficiency: number
  playbookReuse: number
}

export interface GrowthMetrics {
  hitRate: number
  tokenEfficiency: number
  playbookReuse: number
  /** 历史趋势序列（LearningPanel 迷你趋势图数据）。 */
  series?: GrowthSeriesPoint[]
}

// ───────────────────────── 容错解析器 ─────────────────────────

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

export function parseDeadEnds(value: unknown): DeadEndItem[] | null {
  const root = asRecord(value)
  if (root === null) return null
  const items = array(root.items).map((raw, index) => {
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
  return items.length === 0 && !('items' in root) ? null : items
}

export function parseAttackSurface(value: unknown): AttackSurfaceProjection | null {
  const root = asRecord(value)
  if (root === null || !('ports' in root)) return null
  const ports = array(root.ports).map((raw, index) => {
    const row = asRecord(raw) ?? {}
    return {
      port: typeof row.port === 'number' ? row.port : (typeof row.port === 'string' ? Number(row.port) : index),
      protocol: row.protocol !== undefined ? str(row.protocol) : undefined,
      service: row.service !== undefined ? str(row.service) : undefined,
      version: row.version !== undefined ? str(row.version) : undefined,
      state: row.state !== undefined ? str(row.state) : undefined,
      note: row.note !== undefined ? str(row.note) : undefined,
    }
  })
  return {
    targets: root.targets !== undefined ? array(root.targets).map((t) => str(t)).filter((t) => t !== '') : undefined,
    ports,
    services: root.services !== undefined ? array(root.services).map((s) => str(s)).filter((s) => s !== '') : undefined,
  }
}

function parseSeverity(value: unknown): Severity {
  const s = str(value, 'info')
  return s === 'critical' || s === 'high' || s === 'medium' || s === 'low' || s === 'info' ? s : 'info'
}

export function parseReport(value: unknown): ReportProjection | null {
  const root = asRecord(value)
  if (root === null) return null
  const status = str(root.status, 'drafting') === 'ready' ? 'ready' : 'drafting'
  const sections = array(root.sections).map((raw, index) => {
    const section = asRecord(raw) ?? {}
    return { id: str(section.id, `section-${index}`), title: str(section.title, `章节 ${index + 1}`), body: section.body !== undefined ? str(section.body) : undefined }
  })
  const findings = array(root.findings).map((raw, index) => {
    const finding = asRecord(raw) ?? {}
    return {
      id: str(finding.id, `finding-${index}`),
      title: str(finding.title, '未命名发现'),
      severity: parseSeverity(finding.severity),
      summary: finding.summary !== undefined ? str(finding.summary) : undefined,
      evidence: finding.evidence !== undefined ? array(finding.evidence).map((e) => str(e)).filter((e) => e !== '') : undefined,
    }
  })
  return {
    status,
    summary: root.summary !== undefined ? str(root.summary) : undefined,
    sections: sections.length === 0 ? undefined : sections,
    findings: findings.length === 0 ? undefined : findings,
    generatedAt: root.generatedAt !== undefined ? str(root.generatedAt) : undefined,
  }
}

export function parseEvidence(value: unknown): EvidenceChain[] | null {
  const root = asRecord(value)
  if (root === null) return null
  const chains = array(root.chains).map((raw, index) => {
    const chain = asRecord(raw) ?? {}
    return {
      findingId: str(chain.findingId, `finding-${index}`),
      title: str(chain.title, '证据链'),
      severity: parseSeverity(chain.severity),
      nodes: array(chain.nodes).map((nodeRaw, nodeIndex) => {
        const node = asRecord(nodeRaw) ?? {}
        return {
          id: node.id !== undefined ? str(node.id) : undefined,
          at: str(node.at, ''),
          label: str(node.label, `节点 ${nodeIndex + 1}`),
          detail: node.detail !== undefined ? str(node.detail) : undefined,
          kind: node.kind !== undefined ? str(node.kind) : undefined,
        }
      }),
    }
  })
  return chains.length === 0 && !('chains' in root) ? null : chains
}

export function parseGrowth(value: unknown): GrowthMetrics | null {
  const root = asRecord(value)
  if (root === null) return null
  const metrics = asRecord(root.metrics) ?? root
  if (typeof metrics.hitRate !== 'number' && typeof root.hitRate !== 'number') return null
  const series = root.series !== undefined
    ? array(root.series).map((raw) => {
        const point = asRecord(raw) ?? {}
        return {
          label: str(point.label, ''),
          hitRate: num(point.hitRate),
          tokenEfficiency: num(point.tokenEfficiency),
          playbookReuse: num(point.playbookReuse),
        }
      })
    : undefined
  return {
    hitRate: num(metrics.hitRate),
    tokenEfficiency: num(metrics.tokenEfficiency),
    playbookReuse: num(metrics.playbookReuse),
    series: series !== undefined && series.length > 0 ? series : undefined,
  }
}
