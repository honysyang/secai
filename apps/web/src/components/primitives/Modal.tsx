/**
 * Modal：受控居中对话框。overlay portal 到 body（祖先 stacking context 不会
 * 把页面控件顶到遮罩之上）；Esc / 遮罩点击 / 关闭钮关闭。SECAI 内用于完整
 * 报告预览等放大视图。
 */

import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import css from './Modal.module.css'

export interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  closeLabel?: string
  children?: ReactNode
  /** 底部操作行（右对齐）。 */
  footer?: ReactNode
  /** 内容区纵向滚动需要时的附加类。 */
  contentClassName?: string
}

function CloseIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
      <path d="M2 2l8 8M10 2l-8 8" />
    </svg>
  )
}

export function Modal({ open, onClose, title, closeLabel = '关闭', children, footer, contentClassName }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className={css.root} role="presentation">
      <div className={css.mask} aria-hidden="true" onClick={onClose} />
      <div className={css.dialog} role="dialog" aria-modal="true" aria-label={title}>
        <div className={css.header}>
          <h2 className={css.title}>{title}</h2>
          <button type="button" className={css.close} aria-label={closeLabel} onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        {children !== undefined && <div className={`${css.body}${contentClassName !== undefined ? ` ${contentClassName}` : ''}`}>{children}</div>}
        {footer !== undefined && <div className={css.footer}>{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}
