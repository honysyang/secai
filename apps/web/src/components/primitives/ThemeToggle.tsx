/** ThemeToggle：日/夜/跟随系统三态切换。与 index.html 预引导脚本同源（localStorage 键 secai-theme-mode）。 */

import { useEffect, useState, useCallback } from 'react'
import clsx from 'clsx'
import css from './ThemeToggle.module.css'

type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'secai-theme-mode'

function getStored(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch { /* ignore */ }
  return 'system'
}

function applyMode(mode: ThemeMode) {
  const dark =
    mode === 'dark'
      ? true
      : mode === 'light'
        ? false
        : typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  document.body.toggleAttribute('data-secai-dark', dark)
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
      <circle cx="7" cy="7" r="3" />
      <path d="M7 1v1.5M7 11.5V13M1 7h1.5M11.5 7H13M2.8 2.8l1 1M10.2 10.2l1 1M11.2 2.8l-1 1M3.8 10.2l-1 1" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 8.5A5.5 5.5 0 1 1 5.5 2 4.3 4.3 0 0 0 12 8.5Z" />
    </svg>
  )
}

function MonitorIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="1.5" y="2" width="11" height="8" rx="1.5" />
      <path d="M5 12h4M7 10v2" />
    </svg>
  )
}

const MODES: { value: ThemeMode; label: string; icon: () => React.ReactNode }[] = [
  { value: 'light', label: '浅色', icon: () => <SunIcon /> },
  { value: 'dark', label: '深色', icon: () => <MoonIcon /> },
  { value: 'system', label: '跟随系统', icon: () => <MonitorIcon /> },
]

export function ThemeToggle({ className }: { className?: string }) {
  const [mode, setMode] = useState<ThemeMode>(getStored)

  // system 模式下跟随系统变化
  useEffect(() => {
    if (mode !== 'system') return
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => applyMode('system')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [mode])

  const toggle = useCallback(() => {
    const next: ThemeMode = mode === 'light' ? 'dark' : mode === 'dark' ? 'system' : 'light'
    setMode(next)
    try { localStorage.setItem(STORAGE_KEY, next) } catch { /* ignore */ }
    applyMode(next)
  }, [mode])

  const current = MODES.find((m) => m.value === mode) ?? MODES[2]

  return (
    <button
      type="button"
      className={clsx(css.toggle, className)}
      onClick={toggle}
      title={`主题：${current.label}（点击切换）`}
      aria-label={`当前主题 ${current.label}，点击切换`}
    >
      {current.icon()}
    </button>
  )
}
