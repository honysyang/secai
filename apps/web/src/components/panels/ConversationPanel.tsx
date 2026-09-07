// ConversationPanel：工作台右侧对话管理栏（参考图样式：新对话 + 搜索 + 最近对话 + 时间分组）。
// 结构 = 顶部新对话按钮 + 搜索框 + 最近对话分组（今天/昨天/过去七天/更早）
// + 对话行（状态点 + 标题 + 时间 + ⋯ 菜单：重命名/删除）。选中行高亮，点击切换。

import { useMemo, useState } from 'react'
import type { TaskSummary } from '../../runtime/appRuntime.ts'
import { StateDot } from '../primitives/StateDot.tsx'
import type { StateDotState } from '../primitives/StateDot.tsx'
import css from './ConversationPanel.module.css'

export interface ConversationPanelProps {
  tasks: readonly TaskSummary[]
  activeTaskId: string | null
  onSelect: (engagementId: string) => void
  onNewTask: () => void
  onRename: (engagementId: string, title: string) => void
  onDelete: (engagementId: string) => void
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

/** 相对时间分组标签：今天 / 昨天 / 过去七天 / 更早。 */
function timeGroupLabel(iso: string): string {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return '更早'
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const day = new Date(t.getFullYear(), t.getMonth(), t.getDate())
  const diff = Math.floor((today.getTime() - day.getTime()) / 86400000)
  if (diff <= 0) return '今天'
  if (diff === 1) return '昨天'
  if (diff <= 7) return '过去七天'
  return '更早'
}

/** 对话行时间：M月D日 HH:mm。 */
function formatRowTime(iso: string): string {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return ''
  const m = String(t.getMonth() + 1)
  const d = String(t.getDate())
  const hh = String(t.getHours()).padStart(2, '0')
  const mm = String(t.getMinutes()).padStart(2, '0')
  return `${m}月${d}日 ${hh}:${mm}`
}

/** 按时间分组（保持原始 updatedAt 降序序）。 */
function groupByTime(tasks: readonly TaskSummary[]): { label: string; items: TaskSummary[] }[] {
  const groups: { label: string; items: TaskSummary[] }[] = []
  for (const task of tasks) {
    const label = timeGroupLabel(task.updatedAt)
    const last = groups[groups.length - 1]
    if (last !== undefined && last.label === label) {
      last.items.push(task)
    } else {
      groups.push({ label, items: [task] })
    }
  }
  return groups
}

export function ConversationPanel({ tasks, activeTaskId, onSelect, onNewTask, onRename, onDelete }: ConversationPanelProps) {
  const [query, setQuery] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q === '') return tasks
    return tasks.filter(
      (task) => task.title.toLowerCase().includes(q) || task.engagementId.toLowerCase().includes(q),
    )
  }, [tasks, query])

  const groups = useMemo(() => groupByTime(filtered), [filtered])

  const handleRename = (engagementId: string) => {
    const title = renameValue.trim()
    if (title) onRename(engagementId, title)
    setRenamingId(null)
    setRenameValue('')
  }

  return (
    <div className={css.root}>
      {/* 新对话按钮 */}
      <div className={css.topArea}>
        <button type="button" className={css.newBtn} onClick={onNewTask}>
          + 新对话
        </button>
      </div>

      {/* 搜索历史记录 */}
      <div className={css.searchRow}>
        <input
          type="search"
          className={css.search}
          placeholder="搜索历史记录…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {/* 对话列表区 */}
      <div className={css.list}>
        {groups.length === 0 ? (
          <div className={css.empty}>
            {query ? '无匹配对话——调整搜索。' : '暂无对话——点击「+ 新对话」或直接在下方输入目标开始。'}
          </div>
        ) : (
          groups.map((group) => (
            <div key={group.label}>
              {/* 分组标签 */}
              <div className={css.groupLabel}>{group.label}</div>

              {group.items.map((task) => (
                <div
                  key={task.engagementId}
                  className={css.convRow}
                  data-selected={task.engagementId === activeTaskId || undefined}
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
                  {/* 状态点 */}
                  <span className={css.slot}>
                    <StateDot state={taskDotState(task.status)} size={8} />
                  </span>

                  {renamingId === task.engagementId ? (
                    <input
                      type="text"
                      className={css.renameInput}
                      value={renameValue}
                      autoFocus
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={() => handleRename(task.engagementId)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleRename(task.engagementId)
                        if (e.key === 'Escape') {
                          setRenamingId(null)
                          setRenameValue('')
                        }
                        e.stopPropagation()
                      }}
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <span className={css.convTitle} title={task.title}>{task.title}</span>
                  )}

                  {/* 时间 */}
                  <span className={css.timeText}>{formatRowTime(task.updatedAt)}</span>

                  {/* ⋯ 操作菜单 */}
                  <span className={css.menuArea} onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className={css.menuBtn}
                      title="更多操作"
                      onClick={() => setMenuOpenId(menuOpenId === task.engagementId ? null : task.engagementId)}
                    >
                      ⋯
                    </button>

                    {menuOpenId === task.engagementId && (
                      <div className={css.menuDropdown}>
                        <button
                          type="button"
                          className={css.menuItem}
                          onClick={() => {
                            setRenamingId(task.engagementId)
                            setRenameValue(task.title)
                            setMenuOpenId(null)
                          }}
                        >
                          重命名
                        </button>
                        <button
                          type="button"
                          className={css.menuItemDanger}
                          onClick={() => {
                            onDelete(task.engagementId)
                            setMenuOpenId(null)
                          }}
                        >
                          删除
                        </button>
                      </div>
                    )}
                  </span>
                </div>
              ))}
            </div>
          ))
        )}
      </div>

      {/* 点击空白处关闭菜单 */}
      {menuOpenId !== null && (
        <div
          className={css.menuOverlay}
          onClick={() => setMenuOpenId(null)}
          onKeyDown={(e) => { if (e.key === 'Escape') setMenuOpenId(null) }}
          role="presentation"
        />
      )}
    </div>
  )
}
