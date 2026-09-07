/** EmptyState：通用空态占位（居中图标+标题+描述+可选操作）。 */

import type { ReactNode } from 'react'
import clsx from 'clsx'
import css from './EmptyState.module.css'

export interface EmptyStateProps {
  icon?: ReactNode
  title: string
  description?: string
  action?: { label: string; onClick: () => void }
  className?: string
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={clsx(css.root, className)}>
      {icon !== undefined && <div className={css.icon}>{icon}</div>}
      <div className={css.title}>{title}</div>
      {description !== undefined && <div className={css.description}>{description}</div>}
      {action !== undefined && (
        <button type="button" className={css.action} onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  )
}
