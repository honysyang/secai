/**
 * linkline.ts —— 渗透链路 DAG 聚合（test.jpg「渗透」探索链路视图数据源）。
 *
 * 数据源 = 后端 runner 的 `link` 事件（variant=node/edge）：
 * - node：{ id, kind: intent|fact|vuln|asset, key, label, detail, status }
 *   status ∈ open（进行中）/ confirmed（已确认）/ falsified（已判死）。
 * - edge：{ from, to } 因果边（侦察产出事实、事实支撑/判死漏洞、事实挂到意图）。
 *
 * deriveLink 按 seq 升序重放：node 去重（id 覆盖刷新）、edge 去重；
 * 输出稳定拓扑排序（意图→资产→事实→漏洞，层内按首现序）供 DAG 分层渲染。
 */

import type { SessionEvent } from '../connection/api.ts'

/** 链路节点种类（对齐后端 link 事件 kind）。 */
export type LinkKind = 'intent' | 'fact' | 'vuln' | 'asset'

/** 链路节点状态：open 进行中 / confirmed 已确认 / falsified 已判死。 */
export type LinkStatus = 'open' | 'confirmed' | 'falsified'

/** 渗透链路节点。 */
export interface LinkNode {
  id: string
  kind: LinkKind
  key: string
  label: string
  detail: string
  status: LinkStatus
  at: string
}

/** 渗透链路因果边。 */
export interface LinkEdge {
  from: string
  to: string
}

/** deriveLink 聚合产物：节点表 + 边表 + 分类计数。 */
export interface LinkGraph {
  nodes: readonly LinkNode[]
  edges: readonly LinkEdge[]
  counts: Record<LinkKind, number>
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

const KINDS: readonly LinkKind[] = ['intent', 'fact', 'vuln', 'asset']
const KIND_ORDER: Record<LinkKind, number> = { intent: 0, asset: 1, fact: 2, vuln: 3 }

/** 从事件序列聚合渗透链路 DAG（link 事件缺失 = 空图）。 */
export function deriveLink(events: readonly SessionEvent[]): LinkGraph {
  const nodes = new Map<string, LinkNode>()
  const edges: LinkEdge[] = []
  const seen = new Set<string>()

  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.type !== 'link') continue
    const data = asRecord(event.data) ?? {}
    const variant = str(data.variant)
    if (variant === 'node') {
      const raw = asRecord(data.node) ?? {}
      const id = str(raw.id)
      const kind = str(raw.kind) as LinkKind
      if (id === '' || !KINDS.includes(kind)) continue
      const status = str(raw.status, 'open') as LinkStatus
      const node: LinkNode = {
        id,
        kind,
        key: str(raw.key, id),
        label: str(raw.label, id),
        detail: str(raw.detail),
        status: status === 'confirmed' || status === 'falsified' ? status : 'open',
        at: event.createdAt,
      }
      const existing = nodes.get(id)
      if (existing === undefined) {
        nodes.set(id, node)
      } else {
        // 幂等刷新：覆盖可变字段，保留首现时间
        existing.label = node.label
        existing.detail = node.detail || existing.detail
        existing.status = node.status
      }
    } else if (variant === 'edge') {
      const raw = asRecord(data.edge) ?? {}
      const from = str(raw.from)
      const to = str(raw.to)
      if (from === '' || to === '' || from === to) continue
      const key = `${from}->${to}`
      if (seen.has(key)) continue
      seen.add(key)
      edges.push({ from, to })
    }
  }

  // 稳定拓扑：意图→资产→事实→漏洞（层内按首现序）；孤儿节点（无边）排尾部
  const connected = new Set<string>()
  for (const edge of edges) {
    connected.add(edge.from)
    connected.add(edge.to)
  }
  const ordered = [...nodes.values()].sort((a, b) => {
    const conn = Number(connected.has(b.id)) - Number(connected.has(a.id))
    if (conn !== 0) return conn
    const kind = KIND_ORDER[a.kind] - KIND_ORDER[b.kind]
    if (kind !== 0) return kind
    return a.at < b.at ? -1 : a.at > b.at ? 1 : 0
  })

  const counts: Record<LinkKind, number> = { intent: 0, fact: 0, vuln: 0, asset: 0 }
  for (const node of ordered) counts[node.kind] += 1

  return { nodes: ordered, edges, counts }
}

/** 节点种类中文标签。 */
export const LINK_KIND_LABEL: Record<LinkKind, string> = {
  intent: '意图',
  fact: '事实',
  vuln: '漏洞',
  asset: '资产',
}

/** 节点状态中文标签。 */
export const LINK_STATUS_LABEL: Record<LinkStatus, string> = {
  open: '进行中',
  confirmed: '已确认',
  falsified: '已判死',
}
