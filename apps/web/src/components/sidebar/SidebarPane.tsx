/**
 * SidebarPane：左侧目标栏组合（F3）。品牌行 + 目标列表（TargetList，host/
 * session-* 帧驱动）+ 底部外观设置（AppearanceRow 三态）。待审总数以
 * amber Badge 挂列表头。
 */

import type { EngagementSnapshot } from '../../runtime/engagement.ts'
import type { ThemeControl } from '../theme/theme.ts'
import { AppearanceRow } from '../theme/AppearanceRow.tsx'
import { Badge } from '../primitives/Badge.tsx'
import { TargetList } from './TargetList.tsx'
import css from './SidebarPane.module.css'

export interface SidebarPaneProps {
  theme: ThemeControl
  engagement: EngagementSnapshot
  selectedId: string | null
  onSelect: (sessionId: string) => void
}

export function SidebarPane({ theme, engagement, selectedId, onSelect }: SidebarPaneProps) {
  const pending = engagement.pendingApprovals
  return (
    <div className={css.root}>
      <div className={css.brand}>
        <span className={css.word}>SECAI·PT</span>
        <span className={css.caption}>破阵 · 多目标渗透驾驶舱</span>
      </div>
      <div className={css.listHead}>
        <span className={css.listTitle}>目标</span>
        {pending > 0 && (
          <Badge tone="amber" className={css.pendingBadge}>待审 {pending}</Badge>
        )}
        <span className={css.count}>{engagement.totals.targets}</span>
      </div>
      <TargetList sessions={engagement.sessions} selectedId={selectedId} onSelect={onSelect} />
      <div className={css.foot}>
        <div className={css.footLabel}>外观</div>
        <AppearanceRow control={theme} />
      </div>
    </div>
  )
}
