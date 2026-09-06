// InputBar：浮动胶囊输入栏（复刻 dsh ui-conversation InputBar 视觉；
// 不做 dsh 的 backdrop/mirror 芯片三层机制，用简单 textarea 自动增高）。
// 卡片 radius22 + 1px l2-darkmode-thin 边 + input-major 底 + shadow lv2；
// scroll 盒 max-height 14 行；发送钮 34px 圆（info-fill 蓝 + 白箭头），
// 空文本 opacity .4。Enter 发送 / Shift+Enter 换行 / IME 合成期 Enter 不发送。

import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import css from './InputBar.module.css'

export interface InputBarProps {
  disabled: boolean
  disabledReason: string
  onSend: (text: string) => void
}

/** 发送箭头（dsh 图标路径，白 glyph）。 */
function SendArrow() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <path
        d="M8.3125 0.980183C8.66767 1.0531 8.97902 1.20418 9.2627 1.43233C9.48724 1.61297 9.73029 1.85793 9.97949 2.10714L14.707 6.83468L13.293 8.24874L9 3.95577V15.0417H7V3.95577L2.70703 8.24874L1.29297 6.83468L6.02051 2.10714C6.26971 1.85793 6.51277 1.61297 6.7373 1.43233C6.97662 1.23986 7.28445 1.04402 7.6875 0.980183C7.8973 0.947006 8.1031 0.95516 8.3125 0.980183Z"
        fill="currentColor"
      />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M8 2.8v10.4M2.8 8h10.4" />
    </svg>
  )
}

export function InputBar({ disabled, disabledReason, onSend }: InputBarProps) {
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  // IME 合成守卫：拼音选字期的 Enter 不得发送
  const composingRef = useRef(false)

  const empty = draft.trim() === ''

  // 自动增高：内容变化时把高度重置再撑到 scrollHeight（上限由 CSS max-height 管）
  useEffect(() => {
    const el = inputRef.current
    if (el === null) return
    el.style.height = '0px'
    el.style.height = `${el.scrollHeight}px`
  }, [draft])

  const submit = (): void => {
    if (disabled || empty) return
    onSend(draft.trim())
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && e.shiftKey) return // Shift+Enter 原生换行
    if (e.key !== 'Enter') return
    if (composingRef.current || e.nativeEvent.isComposing) return
    e.preventDefault()
    if (e.repeat) return // 长按 Enter 不得连发
    submit()
  }

  return (
    <div className={css.root}>
      {disabled && disabledReason !== '' && (
        <div className={css.notice} role="status">{disabledReason}</div>
      )}
      <div className={css.card} data-composer-card>
        <div className={css.scroll} data-input-scroll>
          <textarea
            ref={inputRef}
            className={css.input}
            value={draft}
            rows={1}
            disabled={disabled}
            placeholder={disabled ? '' : '向该目标下达指令（Enter 发送，Shift+Enter 换行）'}
            aria-label="指令输入"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onCompositionStart={() => { composingRef.current = true }}
            onCompositionEnd={() => { composingRef.current = false }}
          />
        </div>
        <div className={css.row}>
          <div className={css.tools}>
            {/* 预留工具位（dsh 为 + 命令菜单；SECAI 暂无命令体系，占位视觉） */}
            <button type="button" className={css.add} aria-label="工具（预留）" disabled={disabled} tabIndex={-1}>
              <PlusIcon />
            </button>
          </div>
          <div className={css.trailing}>
            <button
              type="button"
              className={css.primary}
              aria-label="发送"
              disabled={disabled || empty}
              onClick={submit}
            >
              <SendArrow />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
