// CommandPalette：Ctrl+K 全局命令面板（按 prototype.html 复刻）。
// 列表分组（导航/操作/任务），键盘 ↑↓/Enter/Esc 操作。

import { useEffect, useRef, useState } from 'react'

export interface Command {
  id: string
  g: string
  t: string
  k?: string
  /** SVG path d=...；为空时用默认箭头。 */
  icon?: string
  onRun: () => void
}

export interface CommandPaletteProps {
  open: boolean
  commands: Command[]
  onClose: () => void
}

export function CommandPalette({ open, commands, onClose }: CommandPaletteProps) {
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open) {
      setQ('')
      setIdx(0)
      setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  const filtered = commands.filter((c) => !q || c.t.toLowerCase().includes(q.toLowerCase()))
  const grouped: Array<{ g: string; items: Command[] }> = []
  filtered.forEach((c) => {
    const last = grouped[grouped.length - 1]
    if (!last || last.g !== c.g) grouped.push({ g: c.g, items: [c] })
    else last.items.push(c)
  })

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, filtered.length - 1)) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)) }
      else if (e.key === 'Enter') {
        e.preventDefault()
        const cmd = filtered[idx]
        if (cmd) { cmd.onRun(); onClose() }
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, filtered, idx, onClose])

  if (!open) return null

  let running = -1
  return (
    <div className="proto-pal-mask on" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="proto-palette" role="dialog" aria-label="命令面板">
        <div className="proto-pal-input-row">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--text-3)' }}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input ref={inputRef} className="proto-pal-input" placeholder="输入命令或搜索…" value={q} onChange={(e) => { setQ(e.target.value); setIdx(0) }} />
          <kbd>Esc</kbd>
        </div>
        <div className="proto-pal-list" ref={listRef}>
          {grouped.length === 0 && <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12.5 }}>无匹配结果</div>}
          {grouped.map((g, gi) => (
            <div key={gi}>
              <div className="proto-pal-group">{g.g}</div>
              {g.items.map((c) => {
                running++
                const active = running === idx
                return (
                  <div
                    key={c.id}
                    className={'proto-pal-item' + (active ? ' on' : '')}
                    onMouseEnter={() => setIdx(running)}
                    onClick={() => { c.onRun(); onClose() }}
                  >
                    <svg className="pi-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={c.icon || 'M3 12h4l3-9 4 18 3-9h4'} /></svg>
                    <span className="pi-t">{c.t}</span>
                    <span className="sp" />
                    {c.k && <kbd>{c.k}</kbd>}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
        <div className="proto-pal-foot">
          <span><kbd>↑↓</kbd> 选择</span>
          <span><kbd>Enter</kbd> 执行</span>
          <span><kbd>Esc</kbd> 关闭</span>
        </div>
      </div>
    </div>
  )
}