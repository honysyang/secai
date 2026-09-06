// SidebarPane：侧栏壳（复刻 dsh ui-sidebar SidebarRoot）。
// 结构：logo 行（品牌字标 + 侧栏收起钮）→ 新建任务钮 → 目标列表区 →
// footer（连接状态徽章 + 外观 ThemeSwitcher 三方块）。
// 收起态（collapsed）：logo 行缩成 36px 圆形展开钮、新建钮变 36px 图标钮、
// 列表与 footer 隐藏（dsh 有 rail 列表，SECAI 目标行 rail 化成本高，本期隐藏）。

import type { EngagementSnapshot } from '../../runtime/engagement.ts'
import type { LinkState } from '../../runtime/appRuntime.ts'
import type { ThemeControl } from '../theme/theme.ts'
import { ThemeSwitcher } from '../theme/ThemeSwitcher.tsx'
import { TargetItem } from './TargetItem.tsx'
import css from './SidebarPane.module.css'

export interface SidebarPaneProps {
  theme: ThemeControl
  engagement: EngagementSnapshot
  selectedId: string | null
  /** 连接状态徽章（demo/connecting/connected/reconnecting）。 */
  link: LinkState
  collapsed: boolean
  onToggle: () => void
  onSelect: (sessionId: string) => void
  /** 打开「新建任务」弹窗。 */
  onNewEngagement: () => void
}

/** 连接状态徽章文案与圆点色。 */
function linkPresentation(link: LinkState): { label: string; tone: 'demo' | 'live' | 'offline' } {
  switch (link) {
    case 'demo':
      return { label: '演示模式', tone: 'demo' }
    case 'connected':
      return { label: '已连接', tone: 'live' }
    case 'connecting':
      return { label: '连接中', tone: 'offline' }
    case 'reconnecting':
      return { label: '重连中', tone: 'offline' }
  }
}

/** 侧栏 logo 字标（SECAI·PT，文字即品牌）。 */
function BrandWordmark() {
  return (
    <span className={css.brand}>
      <span className={css.word}>SECAI·PT</span>
      <span className={css.caption}>破阵</span>
    </span>
  )
}

function PanelLeftIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="1.6" y="2.4" width="12.8" height="11.2" rx="2.2" />
      <path d="M5.8 2.4v11.2" />
    </svg>
  )
}

function PlusIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M8 2.8v10.4M2.8 8h10.4" />
    </svg>
  )
}

export function SidebarPane({
  theme, engagement, selectedId, link, collapsed, onToggle, onSelect, onNewEngagement,
}: SidebarPaneProps) {
  const badge = linkPresentation(link)
  return (
    <div className={css.root} data-collapsed={collapsed || undefined}>
      <div className={css.logoRow}>
        {!collapsed && (
          <button type="button" className={css.brandButton} aria-label="SECAI·PT 新建任务" onClick={onNewEngagement}>
            <BrandWordmark />
          </button>
        )}
        <button
          type="button"
          className={css.iconButton}
          aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
          onClick={onToggle}
        >
          <PanelLeftIcon />
        </button>
      </div>

      <button
        type="button"
        className={css.newSession}
        aria-label="新建任务"
        onClick={onNewEngagement}
      >
        <PlusIcon size={collapsed ? 18 : 14} />
        {!collapsed && <span className={css.newSessionLabel}>新建任务</span>}
      </button>

      {!collapsed && (
        <div className={css.regionArea}>
          {engagement.sessions.length === 0 ? (
            <div className={css.emptyList}>暂无目标——点击「新建任务」下发任务书。</div>
          ) : (
            engagement.sessions.map((session) => (
              <TargetItem
                key={session.sessionId}
                session={session}
                selected={session.sessionId === selectedId}
                onSelect={onSelect}
              />
            ))
          )}
        </div>
      )}

      {!collapsed && (
        <div className={css.footArea}>
          <div className={css.linkBadge} data-tone={badge.tone} role="status">
            <span className={css.linkDot} aria-hidden="true" />
            {badge.label}
          </div>
          <ThemeSwitcher control={theme} />
        </div>
      )}
    </div>
  )
}
