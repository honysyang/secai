/**
 * AppearanceRow：外观设置的三态显式选择行（浅色 / 深色 / 跟随系统），
 * radio 语义。供 sidebar 外观小节使用；与 ThemeToggle 共享同一 ThemeControl。
 */

import type { ThemeControl } from './theme.ts'
import { THEME_CYCLE, THEME_MODE_LABEL } from './theme.ts'
import type { ThemeMode } from './theme.ts'
import { AutoIcon, MoonIcon, SunIcon } from './icons.tsx'
import css from './AppearanceRow.module.css'

function ModeIcon({ mode }: { mode: ThemeMode }) {
  if (mode === 'light') return <SunIcon size={14} />
  if (mode === 'dark') return <MoonIcon size={14} />
  return <AutoIcon size={14} />
}

export function AppearanceRow({ control }: { control: ThemeControl }) {
  return (
    <div className={css.row} role="radiogroup" aria-label="外观模式">
      {THEME_CYCLE.map((mode) => {
        const active = control.mode === mode
        return (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={active}
            className={css.item}
            onClick={() => control.setMode(mode)}
          >
            <span className={css.icon}><ModeIcon mode={mode} /></span>
            <span>{THEME_MODE_LABEL[mode]}</span>
            {active && <span className={css.check} aria-hidden="true" />}
          </button>
        )
      })}
    </div>
  )
}
