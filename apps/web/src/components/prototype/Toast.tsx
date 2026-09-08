// Toast：原型全局 Toast 浮层（fixed 右下，最多 4 条，2.6s 自动消失）。
// 用 module 级 Set 跟踪 id，外部可调用 showToast() 触发。

import { useEffect, useState } from 'react'

interface ToastItem {
  id: number
  msg: string
  color: string
}

let nextId = 1
const listeners = new Set<(items: ToastItem[]) => void>()
let items: ToastItem[] = []

export function showToast(msg: string, type: 'ok' | 'warn' | 'err' | 'info' = 'info') {
  const colorMap: Record<string, string> = {
    ok: 'var(--ok)',
    warn: 'var(--warn)',
    err: 'var(--crit)',
    info: 'var(--brand-600)',
  }
  const color = colorMap[type] ?? 'var(--brand-600)'
  const item: ToastItem = { id: nextId++, msg, color }
  items = [...items.slice(-3), item]
  listeners.forEach((cb) => cb(items))
  setTimeout(() => {
    items = items.filter((it) => it.id !== item.id)
    listeners.forEach((cb) => cb(items))
  }, 2600)
}

export function ToastHost() {
  const [list_, setList] = useState<ToastItem[]>(items)
  useEffect(() => {
    listeners.add(setList)
    return () => { listeners.delete(setList) }
  }, [])
  return (
    <div className="proto-toast-wrap">
      {list_.map((it) => (
        <div key={it.id} className="proto-toast">
          <span className="t-dot" style={{ background: it.color }} />
          {it.msg}
        </div>
      ))}
    </div>
  )
}