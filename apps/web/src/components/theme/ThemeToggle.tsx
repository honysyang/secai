/**
 * ThemeToggle：顶栏用的紧凑外观开关。单按钮按 浅色 → 深色 → 跟随系统 循环，
 * 图标显示当前决议（深色 = 月、否则 = 日；跟随系统时图标随 resolved 变），
 * 完整三态选择见 AppearanceRow。
 */

import type { ThemeControl } from './theme.ts'
import { THEME_CYCLE, THEME_MODE_LABEL } from './theme.ts'
import { MoonIcon, SunIcon } from './icons.tsx'
import css from './ThemeToggle.module.css'

export function ThemeToggle({ control }: { control: ThemeControl }) {
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(control.mode) + 1) % THEME_CYCLE.length]!
  const dark = control.resolved === 'dark'
  return (
    <button
      type="button"
      className={css.root}
      onClick={control.cycle}
      title={`外观：${THEME_MODE_LABEL[control.mode]}，点击切到 ${THEME_MODE_LABEL[next]}`}
      aria-label={`外观模式 ${THEME_MODE_LABEL[control.mode]}，点击切换`}
    >
      {dark ? <MoonIcon size={15} /> : <SunIcon size={15} />}
    </button>
  )
}
