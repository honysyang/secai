/** Toast：轻量全局通知（右上角堆叠，最多 4 条，自动消失）。 */

import { useEffect, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import clsx from 'clsx'
import css from './Toast.module.css'

export type ToastLevel = 'success' | 'info' | 'warn' | 'error'

export interface ToastItem {
  id: number
  message: string
  level: ToastLevel
}

let nextId = 1
const MAX_TOASTS = 4
const listeners = new Set<(items: ToastItem[]) => void>()
let items: ToastItem[] = []

function emit() {
  listeners.forEach((fn) => fn([...items]))
}

export function showToast(message: string, level: ToastLevel = 'info') {
  const item: ToastItem = { id: nextId++, message, level }
  items = [...items, item].slice(-MAX_TOASTS)
  emit()
}

export function ToastHost() {
  const [toasts, setToasts] = useState<ToastItem[]>(items)

  useEffect(() => {
    const listener = (next: ToastItem[]) => setToasts(next)
    listeners.add(listener)
    return () => { listeners.delete(listener) }
  }, [])

  const remove = useCallback((id: number) => {
    items = items.filter((t) => t.id !== id)
    emit()
  }, [])

  return createPortal(
    <div className={css.host} role="region" aria-label="通知">
      {toasts.map((t) => (
        <ToastCard key={t.id} item={t} onDone={() => remove(t.id)} />
      ))}
    </div>,
    document.body,
  )
}

function ToastCard({ item, onDone }: { item: ToastItem; onDone: () => void }) {
  const [leaving, setLeaving] = useState(false)
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    if (paused) return
    const t = setTimeout(() => {
      setLeaving(true)
      setTimeout(onDone, 200)
    }, 4000)
    return () => clearTimeout(t)
  }, [paused, onDone])

  return (
    <div
      className={clsx(css.toast, css[item.level], leaving && css.leaving)}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      role="alert"
    >
      <span className={css.bar} aria-hidden="true" />
      <span className={css.message}>{item.message}</span>
      <button type="button" className={css.close} onClick={() => { setLeaving(true); setTimeout(onDone, 200) }} aria-label="关闭">
        ×
      </button>
    </div>
  )
}
