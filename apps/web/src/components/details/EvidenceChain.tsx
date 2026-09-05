/**
 * EvidenceChain：证据链时间线（F3 details）。数据 = projection.evidence
 * （parseEvidence 的 chains）；每条 finding 一条链：severity 徽章 + 标题 +
 * 节点纵列（rail 线 + 圆点，节点 = 事件快照 label/时间/detail）。
 */

import type { EvidenceChain as EvidenceChainData } from '../../runtime/projections.ts'
import { formatDateTime } from '../../runtime/format.ts'
import { SEVERITY_LABEL } from './severity.ts'
import sevCss from './severity.module.css'
import css from './EvidenceChain.module.css'

export interface EvidenceChainProps {
  chains: readonly EvidenceChainData[] | null
}

export function EvidenceChain({ chains }: EvidenceChainProps) {
  if (chains === null || chains.length === 0) {
    return (
      <div className={css.empty}>
        当前会话尚无证据链投影（projection.evidence）——完成目标的每项 finding 会以一条时间线呈现：从假设、探测到确认的完整事件链。
      </div>
    )
  }
  return (
    <div className={css.root}>
      {chains.map((chain) => (
        <div key={chain.findingId} className={css.chain}>
          <div className={css.chainHead}>
            <span className={sevCss.sev} data-sev={chain.severity}>
              {SEVERITY_LABEL[chain.severity]}
            </span>
            <span className={css.title}>{chain.title}</span>
          </div>
          <ol className={css.nodes}>
            {chain.nodes.map((node, index) => (
              <li key={node.id ?? `${chain.findingId}-${index}`} className={css.node}>
                <div className={css.nodeMain}>
                  <span className={css.nodeLabel}>{node.label}</span>
                  {node.at !== '' && <span className={css.nodeTime}>{formatDateTime(node.at)}</span>}
                </div>
                {node.detail !== undefined && node.detail !== '' && (
                  <div className={css.nodeDetail}>{node.detail}</div>
                )}
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  )
}
