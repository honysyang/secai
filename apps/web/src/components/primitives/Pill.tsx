/** Pill：小号胶囊标签（参照 dsh ui-primitives Pill）。active 态用
 * ghost-active 底 + 描边；传 onClick 即为可交互态（hover 反馈）。 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import clsx from 'clsx'
import css from './Pill.module.css'

export interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean
  children?: ReactNode
}

export function Pill({ active = false, className, children, ...rest }: PillProps) {
  return (
    <button
      type="button"
      className={clsx(css.pill, active && css.active, rest.onClick != null && css.interactive, className)}
      aria-pressed={active || undefined}
      {...rest}
    >
      {children}
    </button>
  )
}
