/** API Key 本地存储 + 未授权事件广播。 */

import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'secai-api-key'

let currentKey: string | null = null
try {
  currentKey = localStorage.getItem(STORAGE_KEY)
} catch { /* ignore */ }

const listeners = new Set<() => void>()
const unauthorizedListeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => fn())
}

export function getApiKey(): string | null {
  return currentKey
}

export function setApiKey(key: string): void {
  currentKey = key
  try {
    localStorage.setItem(STORAGE_KEY, key)
  } catch { /* ignore */ }
  emit()
}

export function clearApiKey(): void {
  currentKey = null
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch { /* ignore */ }
  emit()
}

/** 触发未授权事件（401 / WS 4401）。 */
export function notifyUnauthorized(): void {
  unauthorizedListeners.forEach((fn) => fn())
}

export function onUnauthorized(fn: () => void): () => void {
  unauthorizedListeners.add(fn)
  return () => { unauthorizedListeners.delete(fn) }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 响应式 API Key hook。 */
export function useApiKey(): string | null {
  return useSyncExternalStore(subscribe, () => currentKey, () => currentKey)
}
