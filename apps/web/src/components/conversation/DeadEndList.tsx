/**
 * DeadEndList：死路假设折叠面板（F3）。数据 = projection.dead_ends
 * （runtime/projections.parseDeadEnds）。折叠标题显示计数；展开每项显示
 * 假设、被否原因与翻盘条件（overturn_condition，满足即回收队列）。
 */

import { useState } from 'react'
import type { DeadEndItem } from '../../runtime/projections.ts'
import css from './DeadEndList.module.css'

export interface DeadEndListProps {
  items: readonly DeadEndItem[]
}

export function DeadEndList({ items }: DeadEndListProps) {
  const [open, setOpen] = useState(false)
  if (items.length === 0) return null
  return (
    <section className={css.root}>
      <button
        type="button"
        className={css.toggle}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className={css.title}>已否定的假设</span>
        <span className={css.count}>{items.length}</span>
        <span className={`${css.chev}${open ? ` ${css.chevOpen}` : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <div className={css.body}>
          {items.map((item) => (
            <div key={item.id} className={css.item}>
              <div className={css.stmt}>{item.statement}</div>
              {item.reason !== undefined && item.reason !== '' && (
                <div className={css.reason}>{item.reason}</div>
              )}
              <div className={css.over}>
                <span className={css.overLabel}>翻盘条件</span>
                <span>{item.overturnCondition}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
