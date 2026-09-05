/** Badge：小号状态/计数徽章（圆点 + 文本，dsh 圆点语言）。tone 决定圆点与
 * 底色（tertiary 浅底在亮暗双套各自成立）。 */

import type { ReactNode } from 'react'
import clsx from 'clsx'
import css from './Badge.module.css'

export type BadgeTone = 'neutral' | 'amber' | 'green' | 'blue' | 'red'

export interface BadgeProps {
  tone?: BadgeTone
  /** 是否显示前导圆点（计数徽章可关）。 */
  dot?: boolean
  className?: string
  children?: ReactNode
}

export function Badge({ tone = 'neutral', dot = true, className, children }: BadgeProps) {
  return (
    <span className={clsx(css.badge, css[tone], className)}>
      {dot && <span className={css.dot} aria-hidden="true" />}
      {children}
    </span>
  )
}
