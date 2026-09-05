/** Input：受控单行文本输入（审批备注/设置类用）。多行消息草稿见
 * conversation/InputBar 的自有 textarea 实现。原生 input 属性直通。 */

import type { InputHTMLAttributes } from 'react'
import clsx from 'clsx'
import css from './Input.module.css'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** md = 36px 常规高度，sm = 28px 紧凑。 */
  size?: 'sm' | 'md'
}

export function Input({ size = 'md', className, ...rest }: InputProps) {
  return <input {...rest} className={clsx(css.input, css[size], className)} />
}
