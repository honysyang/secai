/**
 * InputBar：会话指令输入条（F3）。禁用态（无会话/审批挂起/会话已终态）
 * 时 textarea 锁定并显示原因；pendingApprovals > 0 时上方出 amber 提示条
 *（审批提示）。Enter 发送、Shift+Enter 换行；发送后经 onSend 上抛（demo
 * 本地追加轮次 / 联调 steer RPC）。
 */

import { useState } from 'react'
import type { KeyboardEvent } from 'react'
import css from './InputBar.module.css'

export interface InputBarProps {
  disabled: boolean
  /** 禁用原因（同时作 placeholder 与下方状态行文案）。 */
  disabledReason: string
  pendingApprovals: number
  onSend: (text: string) => void
}

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 13.5v-9M4 7.5 8 3.5l4 4" />
    </svg>
  )
}

export function InputBar({ disabled, disabledReason, pendingApprovals, onSend }: InputBarProps) {
  const [text, setText] = useState('')

  const submit = (): void => {
    const value = text.trim()
    if (value === '' || disabled) return
    onSend(value)
    setText('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className={css.root}>
      {pendingApprovals > 0 && (
        <div className={css.notice} role="status">
          {pendingApprovals} 项操作待人工审批——输入已挂起，请先在上方卡片处理
        </div>
      )}
      <div className={css.card} data-disabled={disabled || undefined}>
        <textarea
          className={css.input}
          rows={2}
          value={text}
          disabled={disabled}
          placeholder={disabled ? disabledReason : '下达指令 / 追问当前目标…'}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          aria-label="会话指令"
        />
        <div className={css.row}>
          <span className={css.hint}>{disabled ? disabledReason : 'Enter 发送 · Shift+Enter 换行'}</span>
          <button
            type="button"
            className={css.send}
            disabled={disabled || text.trim() === ''}
            onClick={submit}
            aria-label="发送"
          >
            <SendIcon />
          </button>
        </div>
      </div>
    </div>
  )
}
