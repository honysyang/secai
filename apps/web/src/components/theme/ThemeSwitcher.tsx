// ThemeSwitcher：外观偏好三态选择（复刻 dsh ui-theme AppearanceRow）。
// Light / Dark / System 三个 cube 钮（aria-pressed），选中态跟随持久化
// 偏好而非解析后的当前主题。贴 SECAI 语义（ThemeControl 沿用现有 theme.ts）。

import clsx from 'clsx'
import type { ThemeControl } from './theme.ts'
import type { ThemeMode } from './theme.ts'
import { THEME_MODE_LABEL } from './theme.ts'
import { AutoIcon, MoonIcon, SunIcon } from './icons.tsx'
import css from './ThemeSwitcher.module.css'

const CUBES: readonly { id: ThemeMode; Icon: typeof SunIcon }[] = [
  { id: 'light', Icon: SunIcon },
  { id: 'dark', Icon: MoonIcon },
  { id: 'system', Icon: AutoIcon },
]

export function ThemeSwitcher({ control }: { control: ThemeControl }) {
  return (
    <div className={css.group}>
      <div className={css.title}>外观</div>
      <div className={css.cubeRow}>
        {CUBES.map(({ id, Icon }) => (
          <button
            key={id}
            type="button"
            className={clsx(css.themeCube, control.mode === id && css.selected)}
            aria-pressed={control.mode === id}
            onClick={() => control.setMode(id)}
          >
            <Icon />
            {THEME_MODE_LABEL[id]}
          </button>
        ))}
      </div>
    </div>
  )
}
