import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './base.css'
import { AppFrame } from './components/layout/AppFrame.tsx'

/** 将系统亮暗偏好投影到 body[data-secai-dark] + html color-scheme（token 双套切换点）。 */
function applySystemTheme(): void {
  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  document.body.toggleAttribute('data-secai-dark', dark)
}

// 亮暗跟随系统：启动即应用一次 + 常驻监听（index.html 预引导脚本只覆盖首帧前）
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')
systemDark.addEventListener('change', applySystemTheme)
applySystemTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppFrame />
  </StrictMode>,
)
