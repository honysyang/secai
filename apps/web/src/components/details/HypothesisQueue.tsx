/**
 * HypothesisQueue：推理假设队列（F3 details，顶级推理可解释性）。
 *
 * 呈现当前目标会话的假设队列（deriveHypotheses 派生）：
 * - 每行 = StateDot（active 琥珀追逐 / confirmed 绿 / dead_end 灰）+ 标题 +
 *   置信度条（0-1 渐变宽度 + 高/中/低胶囊）；
 * - 整行可展开（DisclosureRow，参照 ToolRow 折叠卡风格）：展开体 = 证据链
 *   （类型 + 摘要 + 强度）+ 尝试次数 + 死路失败原因/复活条件 / confirmed 铁证；
 * - 空态：「暂无活跃假设」。
 *
 * 数据从事件流/投影解析（runtime/hypotheses.ts），DEMO 与真实模式同一组件。
 */

import { useState } from 'react'
import { DisclosureRow } from '../primitives/DisclosureRow.tsx'
import { StateDot } from '../primitives/StateDot.tsx'
import type { HypothesisStatus, HypothesisView } from '../../runtime/hypotheses.ts'
import { CONFIDENCE_LABEL, HYPOTHESIS_STATUS_LABEL } from '../../runtime/hypotheses.ts'
import css from './HypothesisQueue.module.css'

export interface HypothesisQueueProps {
  /** 派生后的假设队列（deriveHypotheses 产物；空数组 = 无假设）。 */
  hypotheses: readonly HypothesisView[]
}

/** 状态 → StateDot 语义色：active 琥珀 / confirmed 绿 / dead_end 灰（done 绿降饱和近似）。 */
function dotFor(status: HypothesisStatus): 'warning' | 'done' | 'ongoing' {
  switch (status) {
    case 'confirmed':
      return 'done'
    case 'dead_end':
      return 'done' // StateDot 无灰档：dead_end 用降饱和 CSS（data-status=dead_end）
    default:
      return 'warning'
  }
}

/** 单行假设（折叠卡，参照 ToolRow 的行 + 展开体结构）。 */
function HypothesisRow({ view }: { view: HypothesisView }) {
  const [expanded, setExpanded] = useState(false)
  const confidencePct = `${Math.round(view.confidence * 100)}%`
  // 折叠态行内尾部：分隔点 + 状态标签 + 置信度条
  return (
    <div className={css.rowWrap} data-status={view.status}>
      <DisclosureRow
        rowClassName={css.row}
        leadingClassName={css.leading}
        titleClassName={css.title}
        icon={<StateDot state={dotFor(view.status)} size={10} />}
        title={view.title}
        open={expanded}
        expandable
        expandOnRowClick
        keepContentWhenOpen
        onToggle={() => setExpanded(v => !v)}
        collapsedContent={(
          <>
            <span className={css.sep} aria-hidden="true" />
            <span className={css.statusLabel}>{HYPOTHESIS_STATUS_LABEL[view.status]}</span>
            <span className={css.confTrack} aria-hidden="true">
              <span className={css.confFill} data-band={view.band} style={{ width: confidencePct }} />
            </span>
            <span className={css.confText}>{CONFIDENCE_LABEL[view.band]}</span>
          </>
        )}
      >
        {/* 展开体：证据链 + 元信息（点击不冒泡切换，DisclosureRow body 已拦截） */}
        <div className={css.bodyWrap}>
          <div className={css.detailCard}>
            {/* 元信息行：尝试次数 + 优先级 + 精确置信度 */}
            <div className={css.metaLine}>
              <span className={css.metaItem}>尝试 {view.attempts} 次</span>
              {view.priority > 0 && <span className={css.metaItem}>优先级 {view.priority}</span>}
              <span className={css.metaItem}>置信度 {confidencePct}</span>
            </div>
            {/* 证据链：类型 + 摘要 + 强度条 */}
            {view.evidence.length > 0 ? (
              <ol className={css.evidenceList}>
                {view.evidence.map((ev, index) => (
                  <li key={`${ev.type}-${index}`} className={css.evidenceItem}>
                    <span className={css.evType} data-type={ev.type}>{ev.type}</span>
                    <span className={css.evSummary}>{ev.summary}</span>
                    <span className={css.evTrack} aria-hidden="true">
                      <span
                        className={css.evFill}
                        style={{ width: `${Math.round(ev.strength * 100)}%` }}
                      />
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <div className={css.noEvidence}>暂无关联证据——等待事件流/投影沉淀。</div>
            )}
            {/* 死路：失败原因 + 复活条件 */}
            {view.status === 'dead_end' && view.deadReason !== undefined && view.deadReason !== '' && (
              <div className={css.deadReason}>
                <span className={css.deadLabel}>失败原因</span>
                {view.deadReason}
              </div>
            )}
            {view.status === 'dead_end' && view.overturnCondition !== undefined && view.overturnCondition !== '' && (
              <div className={css.overturn}>
                <span className={css.deadLabel}>复活条件</span>
                {view.overturnCondition}
              </div>
            )}
            {/* confirmed：铁证提示 */}
            {view.status === 'confirmed' && (
              <div className={css.confirmedNote}>
                已验证成立——证据链完整留存，随报告归档。
              </div>
            )}
          </div>
        </div>
      </DisclosureRow>
    </div>
  )
}

export function HypothesisQueue({ hypotheses }: HypothesisQueueProps) {
  if (hypotheses.length === 0) {
    return (
      <div className={css.empty}>
        暂无活跃假设——agent 提出假设后会在此呈现：标题、置信度、证据链与死路归档。
      </div>
    )
  }
  return (
    <div className={css.root}>
      {hypotheses.map((view) => (
        <HypothesisRow key={view.id} view={view} />
      ))}
    </div>
  )
}
