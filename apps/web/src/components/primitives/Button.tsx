/**
 * Button：token 化按钮原子（参照 dsh ui-primitives Button）。
 * variant 映射 --secai-alias-button-* 填充族；尺寸 md=36px 胶囊 / sm=28px
 * 紧凑；icon 为可选的 16px 前导节点。原生 button 属性直通。
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import clsx from 'clsx'
import css from './Button.module.css'

/** 视觉族：primary 墨色主操作 / info 品牌蓝（发送、允许）/ ghost 透明 /
 * outline 描边胶囊（取消）。 */
export type ButtonVariant = 'primary' | 'info' | 'ghost' | 'outline'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'md' | 'sm'
  /** 可选前导 16px 图标节点。 */
  icon?: ReactNode
}

export function Button({
  variant = 'ghost',
  size = 'md',
  icon,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button type="button" className={clsx(css.button, css[variant], css[size], className)} {...rest}>
      {icon != null && <span className={css.icon}>{icon}</span>}
      {children}
    </button>
  )
}
