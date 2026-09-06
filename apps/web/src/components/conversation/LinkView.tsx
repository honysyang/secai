// LinkView：渗透链路视图（test.jpg「渗透」探索链路，意图·事实·漏洞·资产 DAG）。
// 布局：意图根在左，资产/事实/漏洞节点按列分层向右展开（意图→资产→事实→漏洞），
// SVG 贝塞尔连线表达因果；节点卡片按种类着色（意图=品牌蓝/资产=中性/事实=琥珀/
// 漏洞=红），状态点区分 进行中(脉冲)/已确认(✓)/已判死(✕)。点击节点展开 detail。

import { useMemo, useState } from 'react'
import type { SessionEvent } from '../../connection/api.ts'
import { deriveLink } from '../../runtime/linkline.ts'
import type { LinkNode } from '../../runtime/linkline.ts'
import { LINK_KIND_LABEL, LINK_STATUS_LABEL } from '../../runtime/linkline.ts'
import css from './LinkView.module.css'

export interface LinkViewProps {
  events: readonly SessionEvent[]
  /** 空态文案（无链路节点时）。 */
  emptyText?: string
}

/** 列宽（px）：意图根 / 资产 / 事实 / 漏洞 各占一列，节点卡片宽固定。 */
const COL_X = [0, 240, 480, 720]
const NODE_W = 200
const NODE_GAP = 16
const NODE_H = 56
const PAD_TOP = 24

/** 状态点：进行中脉冲 / 已确认 ✓ / 已判死 ✕。 */
function StatusDot({ status }: { status: LinkNode['status'] }) {
  if (status === 'open') return <span className={css.pulse} aria-label="进行中" />
  return (
    <span className={css.statusDot} data-status={status} aria-hidden="true">
      {status === 'confirmed' ? (
        <svg width="8" height="8" viewBox="0 0 8 8" fill="none" stroke="currentColor"
          strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M1.6 4.2L3.2 5.8L6.4 2.4" />
        </svg>
      ) : (
        <svg width="8" height="8" viewBox="0 0 8 8" fill="none" stroke="currentColor"
          strokeWidth="1.6" strokeLinecap="round">
          <path d="M2 2L6 6M6 2L2 6" />
        </svg>
      )}
    </span>
  )
}

/** 单个链路节点卡片。 */
function LinkCard({ node, onToggle, open }: { node: LinkNode; onToggle: () => void; open: boolean }) {
  return (
    <div className={css.card} data-kind={node.kind} data-status={node.status}>
      <button type="button" className={css.cardHead} aria-expanded={open} onClick={onToggle}>
        <StatusDot status={node.status} />
        <span className={css.kindTag}>{LINK_KIND_LABEL[node.kind]}</span>
        <span className={css.cardTitle} title={node.label}>{node.label}</span>
      </button>
      {open && node.detail !== '' && <pre className={css.cardDetail}>{node.detail}</pre>}
    </div>
  )
}

export function LinkView({
  events,
  emptyText = '尚无渗透链路——下发任务后意图 / 侦察资产 / 确认事实 / 漏洞会按因果关系连成链路图。',
}: LinkViewProps) {
  const graph = useMemo(() => deriveLink(events), [events])
  const [openId, setOpenId] = useState<string | null>(null)

  // 布局：按拓扑序列把节点分配到列；列内纵向堆叠，有子节点的节点排在父节点附近。
  const layout = useMemo(() => {
    const pos = new Map<string, { x: number; y: number }>()
    const colCount = [0, 0, 0, 0] // intent/asset/fact/vuln
    // 目标列：vuln=3，fact=2，asset=1，intent=0
    const colOf = (node: LinkNode): number =>
      node.kind === 'vuln' ? 3 : node.kind === 'fact' ? 2 : node.kind === 'asset' ? 1 : 0
    // 先按节点顺序分配初始列
    for (const node of graph.nodes) {
      const col = colOf(node)
      pos.set(node.id, { x: COL_X[col] ?? 0, y: PAD_TOP + colCount[col] * (NODE_H + NODE_GAP) })
      colCount[col] += 1
    }
    // 有入边的节点：若其列 <= 父节点列（可能造成回边/重叠），保持在语义列即可；
    // 本实现采用固定语义列（意图→资产→事实→漏洞），连线用贝塞尔表达因果。
    const height = Math.max(...colCount.map((c) => PAD_TOP * 2 + c * (NODE_H + NODE_GAP)), 200)
    const width = (COL_X[3] ?? 720) + NODE_W + PAD_TOP
    return { pos, width, height }
  }, [graph])

  if (graph.nodes.length === 0) {
    return <div className={css.empty}>{emptyText}</div>
  }

  return (
    <div className={css.root}>
      <div className={css.toolbar}>
        <span className={css.metric}>意图 {graph.counts.intent}</span>
        <span className={css.metric}>事实 {graph.counts.fact}</span>
        <span className={css.metric} data-tone="error">漏洞 {graph.counts.vuln}</span>
        <span className={css.metric}>资产 {graph.counts.asset}</span>
        <span className={css.metric} data-tone="dim">{graph.edges.length} 条因果</span>
      </div>
      <div className={css.scroll}>
        <div className={css.canvas} style={{ width: layout.width, height: layout.height }}>
          <svg
            className={css.svg}
            width={layout.width}
            height={layout.height}
            aria-hidden="true"
          >
            {graph.edges.map((edge) => {
              const a = layout.pos.get(edge.from)
              const b = layout.pos.get(edge.to)
              if (!a || !b) return null
              const x1 = a.x + NODE_W
              const y1 = a.y + NODE_H / 2
              const x2 = b.x
              const y2 = b.y + NODE_H / 2
              const dx = Math.max(40, (x2 - x1) / 2)
              return (
                <path
                  key={`${edge.from}->${edge.to}`}
                  className={css.edge}
                  d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
                />
              )
            })}
          </svg>
          {graph.nodes.map((node) => {
            const p = layout.pos.get(node.id)
            if (!p) return null
            return (
              <div key={node.id} className={css.cardWrap} style={{ left: p.x, top: p.y, width: NODE_W }}>
                <LinkCard
                  node={node}
                  open={openId === node.id}
                  onToggle={() => setOpenId((cur) => (cur === node.id ? null : node.id))}
                />
                <span className={css.statusLabel} data-status={node.status}>
                  {LINK_STATUS_LABEL[node.status]}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
