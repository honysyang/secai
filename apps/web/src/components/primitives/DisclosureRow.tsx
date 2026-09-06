// DisclosureRow：单行 24px 展开行原语（复刻 dsh ui-primitives DisclosureRow）。
// [16px 前导槽] gap6 [title 14/24] [chevron] + 行内尾部内容（截断摘要）；
// 整行 = 展开开关（点击 / Enter / Space），展开体从行下方滑出。
// 展开态前导槽图标→chevron 的悬停预览由调用方 CSS 控制（.chevronClassName）。

import { useState, type KeyboardEvent, type ReactNode } from 'react'
import clsx from 'clsx'
import css from './DisclosureRow.module.css'

export interface DisclosureRowProps {
  /** 行容器附加类（皮肤：扫光带等挂这里）。 */
  rowClassName?: string | undefined
  leadingClassName?: string | undefined
  titleClassName?: string | undefined
  chevronClassName?: string | undefined
  /** 16px 前导节点（状态点/图标）；悬停/展开时可由 CSS 换成 chevron。 */
  icon: ReactNode
  title: ReactNode
  /** 受控展开态。 */
  open: boolean
  /** 是否有展开体（无则不渲染 chevron/开关行为）。 */
  expandable: boolean
  /** 整行点击即切换（工具行 true；菜单类行 false 用独立按钮）。 */
  expandOnRowClick?: boolean | undefined
  /** 展开后保持挂载（滚动位置/内部状态保留）。 */
  keepContentWhenOpen?: boolean | undefined
  onToggle?: (() => void) | undefined
  /** 折叠态行内尾部（分隔点 + 截断摘要等）。 */
  collapsedContent?: ReactNode
  /** 展开体（行下方滑出；点击不冒泡切换）。 */
  children?: ReactNode
}

/** 展开箭头（12px，向下；展开态旋转 180°）。 */
function ChevronDown({ className }: { className?: string | undefined }) {
  return (
    <svg className={clsx(css.chevronIcon, className)} width="12" height="12" viewBox="0 0 12 12"
      fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M3 4.5L6 7.5L9 4.5" />
    </svg>
  )
}

export function DisclosureRow({
  rowClassName,
  leadingClassName,
  titleClassName,
  chevronClassName,
  icon,
  title,
  open,
  expandable,
  expandOnRowClick = false,
  keepContentWhenOpen = false,
  onToggle,
  collapsedContent,
  children,
}: DisclosureRowProps) {
  const [innerOpen, setInnerOpen] = useState(false)
  const controlled = onToggle !== undefined
  const openState = controlled ? open : innerOpen

  const toggle = (): void => {
    if (!expandable) return
    if (controlled) onToggle()
    else setInnerOpen(v => !v)
  }

  const onRowClick = (): void => {
    if (expandable && expandOnRowClick) toggle()
  }
  const onRowKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (!expandable || !expandOnRowClick) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggle()
    }
  }

  const showBody = openState && expandable
  const mountBody = showBody || keepContentWhenOpen

  return (
    <div className={css.root}>
      <div
        className={clsx(css.row, rowClassName)}
        role={expandable ? 'button' : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-expanded={expandable ? showBody : undefined}
        data-open={showBody || undefined}
        data-expandable={expandable || undefined}
        onClick={onRowClick}
        onKeyDown={onRowKeyDown}
      >
        <span className={clsx(css.leading, leadingClassName)} data-slot="leading">
          <span className={css.leadingIcon} data-slot="leading-icon">{icon}</span>
          {expandable && (
            <span className={clsx(css.leadingChevron, chevronClassName)} data-slot="leading-chevron">
              <ChevronDown />
            </span>
          )}
        </span>
        <span className={clsx(css.title, titleClassName)}>{title}</span>
        {collapsedContent}
        {expandable && !expandOnRowClick && (
          <button type="button" className={css.trailingButton} aria-label={showBody ? '收起' : '展开'} onClick={toggle}>
            <ChevronDown />
          </button>
        )}
      </div>
      {mountBody && (
        <div className={css.body} data-open={showBody || undefined} onClick={(e) => { e.stopPropagation() }}>
          {children}
        </div>
      )}
    </div>
  )
}
