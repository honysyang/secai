/**
 * HypothesisQueue：假设队列排序条（F3）。数据 = session/queue 帧
 * （snapshot.queue 的 QueueItem[]，含 priority/status/attempts）。
 * 渲染时按 priority 降序（同级按 attempts 少者先）；testing 高亮、
 * validated 绿边、falsified 划线降透明。热项（priority >= 8）徽章转 amber。
 */

import type { QueueItem } from '../../connection/api.ts'
import css from './HypothesisQueue.module.css'

/** 假设状态 → 短标签。 */
export const HYPOTHESIS_STATUS_LABEL: Record<string, string> = {
  pending: '待验',
  testing: '验证中',
  validated: '成立',
  falsified: '证伪',
  inconclusive: '无定论',
}

/** priority 热区阈值：>= 8 视为高优假设。 */
export const HOT_PRIORITY = 8

export interface HypothesisQueueProps {
  items: readonly QueueItem[]
}

export function HypothesisQueue({ items }: HypothesisQueueProps) {
  const sorted = [...items].sort((a, b) => b.priority - a.priority || a.attempts - b.attempts)
  return (
    <section className={css.root} aria-label="假设队列">
      <div className={css.header}>
        <span>假设队列</span>
        <span className={css.count}>{items.length}</span>
      </div>
      <div className={css.chips}>
        {sorted.map((item) => (
          <span key={item.id} className={css.chip} data-status={item.status} data-hot={item.priority >= HOT_PRIORITY || undefined}>
            <span className={css.pri}>{item.priority}</span>
            <span className={css.stmt} title={item.statement}>{item.statement}</span>
            <span className={css.tag}>{HYPOTHESIS_STATUS_LABEL[item.status] ?? item.status}</span>
          </span>
        ))}
      </div>
    </section>
  )
}
