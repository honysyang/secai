/**
 * F2 主题三态控制器（纯逻辑 + 一个 React hook）：
 *
 * - 模式：'light' | 'dark' | 'system'。显式两态立即定死；system 态经
 *   matchMedia('(prefers-color-scheme: dark)') 常驻监听，跟随系统变化。
 * - 落地：把决议写 body[data-secai-dark] + html color-scheme —— 这正是
 *   design-platform.css 双套 token 的切换点；index.html 预引导 script 按
 *   同一存储 key 在首帧前做等价应用（防闪跳），本模块负责运行期一致。
 * - 持久化：localStorage（key = THEME_STORAGE_KEY），刷新后保持用户选择。
 */

import { useCallback, useEffect, useState } from 'react'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

/** 存储 key（index.html 预引导 script 也读它）。 */
export const THEME_STORAGE_KEY = 'secai-theme-mode'
/** 循环切换顺序：浅色 → 深色 → 跟随系统 → … */
export const THEME_CYCLE: readonly ThemeMode[] = ['light', 'dark', 'system']
/** 面向用户的模式名（界面文案）。 */
export const THEME_MODE_LABEL: Record<ThemeMode, string> = {
  light: '浅色',
  dark: '深色',
  system: '跟随系统',
}

/** 读取持久化模式；非法值/缺省回落 system。 */
export function readThemeMode(): ThemeMode {
  const stored = localStorage.getItem(THEME_STORAGE_KEY)
  return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
}

/** 解析为实际渲染主题：system 按系统偏好展开。 */
export function resolveThemeMode(mode: ThemeMode, systemDark: boolean): ResolvedTheme {
  return mode === 'system' ? (systemDark ? 'dark' : 'light') : mode
}

/** 系统当前是否偏好深色。 */
export function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/** 把决议主题落到 DOM：body[data-secai-dark] + color-scheme。 */
export function applyThemeMode(resolved: ResolvedTheme): void {
  document.documentElement.style.colorScheme = resolved
  document.body.toggleAttribute('data-secai-dark', resolved === 'dark')
}

/** 主题控制句柄（App 层持有一次，props 下传共享）。 */
export interface ThemeControl {
  mode: ThemeMode
  resolved: ResolvedTheme
  setMode: (mode: ThemeMode) => void
  /** 按 THEME_CYCLE 顺序推进一档（ThemeToggle 用）。 */
  cycle: () => void
}

/** 主题三态 hook：状态 + matchMedia 监听 + DOM 落地 + 持久化。 */
export function useThemeControl(): ThemeControl {
  const [mode, setModeState] = useState<ThemeMode>(readThemeMode)
  const [systemDark, setSystemDark] = useState(systemPrefersDark)

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (): void => setSystemDark(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const resolved = resolveThemeMode(mode, systemDark)
  useEffect(() => {
    applyThemeMode(resolved)
  }, [resolved])

  const setMode = useCallback((next: ThemeMode) => {
    localStorage.setItem(THEME_STORAGE_KEY, next)
    setModeState(next)
  }, [])

  const cycle = useCallback(() => {
    const index = THEME_CYCLE.indexOf(mode)
    setMode(THEME_CYCLE[(index + 1) % THEME_CYCLE.length]!)
  }, [mode, setMode])

  return { mode, resolved, setMode, cycle }
}
