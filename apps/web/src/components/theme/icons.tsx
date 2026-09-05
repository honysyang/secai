/**
 * 主题用极简内联图标（浅色/深色/跟随系统）。均以 currentColor 描边，
 * 颜色跟随所在按钮的 color token，不引入图标库。
 */

interface IconProps {
  size?: number
}

export function SunIcon({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.3" strokeLinecap="round" aria-hidden="true">
      <circle cx="8" cy="8" r="3.1" />
      <path d="M8 1.4v1.7M8 12.9v1.7M1.4 8h1.7M12.9 8h1.7M3.3 3.3l1.2 1.2M11.5 11.5l1.2 1.2M12.7 3.3l-1.2 1.2M4.5 11.5l-1.2 1.2" />
    </svg>
  )
}

export function MoonIcon({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M13.7 9.8A6 6 0 0 1 6.2 2.3a6 6 0 1 0 7.5 7.5Z" />
    </svg>
  )
}

export function AutoIcon({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.3" strokeLinecap="round" aria-hidden="true">
      <rect x="1.6" y="2.5" width="12.8" height="8.8" rx="1.8" />
      <path d="M5.6 14.3h4.8M8 11.3v3" />
    </svg>
  )
}
