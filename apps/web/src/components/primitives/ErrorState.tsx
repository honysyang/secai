/** ErrorState：错误态占位（红色系 + 重试按钮）。 */

import type { ReactNode } from 'react'
import clsx from 'clsx'
import css from './ErrorState.module.css'

export interface ErrorStateProps {
  icon?: ReactNode
  title?: string
  description?: string
  onRetry: () => void
  className?: string
}

function DefaultErrorIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  )
}

export function ErrorState({ icon, title = '出错了', description, onRetry, className }: ErrorStateProps) {
  return (
    <div className={clsx(css.root, className)}>
      <div className={css.icon}>{icon ?? <DefaultErrorIcon />}</div>
      <div className={css.title}>{title}</div>
      {description !== undefined && <div className={css.description}>{description}</div>}
      <button type="button" className={css.retry} onClick={onRetry}>
        重试
      </button>
    </div>
  )
}
