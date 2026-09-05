import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './base.css'
import App from './App.tsx'
import {
  applyThemeMode,
  readThemeMode,
  resolveThemeMode,
  systemPrefersDark,
} from './components/theme/theme.ts'

// 首帧即按持久化模式（或 system）落地主题；运行期由 App 内 useThemeControl
// 接管（index.html 预引导 script 保证首帧前无闪跳，此处为同源兜底）。
applyThemeMode(resolveThemeMode(readThemeMode(), systemPrefersDark()))

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
