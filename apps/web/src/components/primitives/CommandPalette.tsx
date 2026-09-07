/** CommandPalette：IDE 级全局命令面板（Ctrl/Cmd+K）。 */

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import clsx from 'clsx'
import css from './CommandPalette.module.css'

export interface Command {
  id: string
  label: string
  hint?: string
  group: '导航' | '任务' | '工具' | '设置'
  keywords: string[]
  onRun: () => void
}

export interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  commands: Command[]
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [highlighted, setHighlighted] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 过滤 + 分组
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q === '') return commands
    return commands.filter(
      (cmd) =>
        cmd.label.toLowerCase().includes(q) ||
        cmd.keywords.some((k) => k.toLowerCase().includes(q)),
    )
  }, [commands, query])

  const groups = useMemo(() => {
    const map = new Map<string, Command[]>()
    for (const cmd of filtered) {
      const list = map.get(cmd.group) ?? []
      list.push(cmd)
      map.set(cmd.group, list)
    }
    return [...map.entries()]
  }, [filtered])

  // 重置高亮
  useEffect(() => {
    setHighlighted(0)
  }, [query])

  // 自动聚焦
  useEffect(() => {
    if (open) {
      setQuery('')
      setHighlighted(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  // 键盘导航
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setHighlighted((h) => Math.min(h + 1, filtered.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setHighlighted((h) => Math.max(h - 1, 0))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const cmd = filtered[highlighted]
        if (cmd) {
          cmd.onRun()
          onClose()
        }
      }
    },
    [filtered, highlighted, onClose],
  )

  // 全局监听 Ctrl/Cmd+K
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        if (open) {
          onClose()
        } else {
          // 触发自定义事件，由 App.tsx 处理打开
          window.dispatchEvent(new CustomEvent('secai:open-palette'))
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 滚动高亮项到可视区
  useEffect(() => {
    if (!open || !listRef.current) return
    const items = listRef.current.querySelectorAll('[data-cmd-index]')
    const el = items[highlighted] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [highlighted, open])

  if (!open) return null

  let flatIndex = -1

  return createPortal(
    <div className={css.root} role="presentation">
      <div className={css.mask} aria-hidden="true" onClick={onClose} />
      <div className={css.dialog} role="dialog" aria-modal="true" aria-label="命令面板">
        {/* 搜索输入 */}
        <div className={css.searchRow}>
          <svg className={css.searchIcon} width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" />
            <path d="M10.5 10.5L14 14" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            className={css.searchInput}
            placeholder="搜索命令、资产、工具…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <kbd className={css.kbd}>Esc</kbd>
        </div>

        {/* 命令列表 */}
        <div className={css.list} ref={listRef}>
          {groups.length === 0 ? (
            <div className={css.empty}>无匹配命令</div>
          ) : (
            groups.map(([groupName, cmds]) => (
              <div key={groupName}>
                <div className={css.groupLabel}>{groupName}</div>
                {cmds.map((cmd) => {
                  flatIndex++
                  const idx = flatIndex
                  return (
                    <div
                      key={cmd.id}
                      data-cmd-index={idx}
                      className={clsx(css.item, idx === highlighted && css.highlighted)}
                      onMouseEnter={() => setHighlighted(idx)}
                      onClick={() => {
                        cmd.onRun()
                        onClose()
                      }}
                    >
                      <span className={css.itemLabel}>{cmd.label}</span>
                      {cmd.hint !== undefined && <span className={css.itemHint}>{cmd.hint}</span>}
                    </div>
                  )
                })}
              </div>
            ))
          )}
        </div>

        {/* 底部提示 */}
        <div className={css.footer}>
          <span><kbd>↑↓</kbd> 导航</span>
          <span><kbd>Enter</kbd> 执行</span>
          <span><kbd>Esc</kbd> 关闭</span>
        </div>
      </div>
    </div>,
    document.body,
  )
}
