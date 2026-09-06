// SidebarPane：侧栏壳（复刻 dsh ui-sidebar SidebarRoot）。
// 结构：logo 行（品牌字标 + 侧栏收起钮）→ 导航菜单（工作台/资产/风险/报告）
// → 任务列表（engagement 行，仿 dsh 会话列表）→ 目标会话列表（活动任务成员）
// → footer（连接状态徽章）。「新建任务」由输入栏 + 按钮触发。
// 收起态（collapsed）：logo 行缩成 36px 圆形展开钮、菜单变图标、列表与
// footer 隐藏。

import type { EngagementSnapshot } from '../../runtime/engagement.ts'
import type { LinkState, RouteKey, TaskSummary } from '../../runtime/appRuntime.ts'
import { formatClock } from '../../runtime/format.ts'
import { StateDot } from '../primitives/StateDot.tsx'
import type { StateDotState } from '../primitives/StateDot.tsx'
import { TargetItem } from './TargetItem.tsx'
import css from './SidebarPane.module.css'

export interface SidebarPaneProps {
  engagement: EngagementSnapshot
  selectedId: string | null
  /** 任务列表（engagement 行，updatedAt 降序）。 */
  tasks: readonly TaskSummary[]
  /** 活动任务书集群 id（null = 尚未选择）。 */
  activeTaskId: string | null
  /** 连接状态徽章（connecting/connected/reconnecting）。 */
  link: LinkState
  /** 当前顶层路由（导航菜单高亮）。 */
  route: RouteKey
  collapsed: boolean
  onToggle: () => void
  onSelect: (sessionId: string) => void
  /** 选中任务（任务列表行点击）。 */
  onSelectTask: (engagementId: string) => void
  /** 切换导航路由。 */
  onRoute: (route: RouteKey) => void
}

/** 导航菜单项定义。 */
const NAV_ITEMS: ReadonlyArray<{ key: RouteKey; label: string; icon: () => React.ReactNode }> = [
  { key: 'workbench', label: '工作台', icon: () => <WorkbenchIcon /> },
  { key: 'assets', label: '资产', icon: () => <AssetsIcon /> },
  { key: 'risks', label: '风险', icon: () => <RisksIcon /> },
  { key: 'reports', label: '报告', icon: () => <ReportsIcon /> },
]

function WorkbenchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="1.6" y="2.4" width="12.8" height="11.2" rx="2.2" />
      <path d="M5.8 2.4v11.2" />
    </svg>
  )
}

function AssetsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="2" width="5" height="5" rx="1" />
      <rect x="9" y="2" width="5" height="5" rx="1" />
      <rect x="2" y="9" width="5" height="5" rx="1" />
      <rect x="9" y="9" width="5" height="5" rx="1" />
    </svg>
  )
}

function RisksIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 1.8L14.5 13.5H1.5L8 1.8Z" />
      <path d="M8 6.5v3.5" />
      <circle cx="8" cy="12" r="0.5" fill="currentColor" />
    </svg>
  )
}

function ReportsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 1.8h5.5L13 5.3v9H4V1.8Z" />
      <path d="M9.5 1.8v3.5H13" />
      <path d="M6 8.5h4M6 11h4" />
    </svg>
  )
}

/** 连接状态徽章文案与圆点色。 */
function linkPresentation(link: LinkState): { label: string; tone: 'live' | 'offline' } {
  switch (link) {
    case 'connected':
      return { label: '已连接', tone: 'live' }
    case 'connecting':
      return { label: '连接中', tone: 'offline' }
    case 'reconnecting':
      return { label: '重连中', tone: 'offline' }
  }
}

/** 任务书状态 → StateDot 四态（running 蓝 / completed 绿 / failed 红）。 */
function taskDotState(status: TaskSummary['status']): StateDotState {
  switch (status) {
    case 'running':
      return 'ongoing'
    case 'completed':
      return 'done'
    case 'failed':
      return 'error'
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

/** 任务列表行：状态点 + 标题 + 目标数徽标 + 时间（仿 dsh sessionRow 几何）。 */
function TaskItem({
  task,
  active,
  onSelect,
}: {
  task: TaskSummary
  active: boolean
  onSelect: (engagementId: string) => void
}) {
  return (
    <div
      className={css.sessionRow}
      data-selected={active || undefined}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(task.engagementId)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(task.engagementId)
        }
      }}
    >
      <span className={css.slot}>
        <StateDot state={taskDotState(task.status)} size={10} />
      </span>
      <span className={css.title} title={task.title}>{task.title}</span>
      {task.sessionCount > 0 && <span className={css.countBadge}>{task.sessionCount}</span>}
      <span className={css.time} data-time="">{formatClock(task.updatedAt)}</span>
    </div>
  )
}

export function SidebarPane({
  engagement, selectedId, tasks, activeTaskId, link, route, collapsed, onToggle, onSelect, onSelectTask, onRoute,
}: SidebarPaneProps) {
  const badge = linkPresentation(link)
  return (
    <div className={css.root} data-collapsed={collapsed || undefined}>
      <div className={css.logoRow}>
        {!collapsed && (
          <div className={css.brandWord}>
            <BrandWordmark />
          </div>
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

      {/* 导航菜单 */}
      <nav className={css.navMenu} aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`${css.navItem} ${route === item.key ? css.navItemActive : ''}`}
            aria-current={route === item.key ? 'page' : undefined}
            onClick={() => onRoute(item.key)}
          >
            <span className={css.navIcon}>{item.icon()}</span>
            {!collapsed && <span className={css.navLabel}>{item.label}</span>}
          </button>
        ))}
      </nav>

      {!collapsed && (
        <div className={css.regionArea}>
          {/* 任务列表（顶层：一个任务书 = 一次渗透任务） */}
          <div className={css.sectionLabel}>任务</div>
          {tasks.length === 0 ? (
            <div className={css.emptyList}>暂无任务——在对话框输入目标，回车直接开始。</div>
          ) : (
            tasks.map((task) => (
              <TaskItem
                key={task.engagementId}
                task={task}
                active={task.engagementId === activeTaskId}
                onSelect={onSelectTask}
              />
            ))
          )}

          {/* 目标会话列表（活动任务的成员目标） */}
          {engagement.sessions.length > 0 && (
            <>
              <div className={css.sectionLabel}>目标会话</div>
              {engagement.sessions.map((session) => (
                <TargetItem
                  key={session.sessionId}
                  session={session}
                  selected={session.sessionId === selectedId}
                  onSelect={onSelect}
                />
              ))}
            </>
          )}
        </div>
      )}

      {!collapsed && (
        <div className={css.footArea}>
          <div className={css.linkBadge} data-tone={badge.tone} role="status">
            <span className={css.linkDot} aria-hidden="true" />
            {badge.label}
          </div>
        </div>
      )}
    </div>
  )
}
