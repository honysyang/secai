// TaskPanel：工作台右侧任务管理栏（会话管理实质——任务书 = 一次渗透任务）。
// 结构 = 头部（标题 + 新建按钮）+ 搜索框 + 任务列表（状态点 + 标题 +
// 目标数徽标 + 时间 + 操作按钮：重命名/删除）。选中行高亮，点击切换活动任务。

import { useMemo, useState } from 'react'
import type { TaskSummary } from '../../runtime/appRuntime.ts'
import { formatClock } from '../../runtime/format.ts'
import { StateDot } from '../primitives/StateDot.tsx'
import type { StateDotState } from '../primitives/StateDot.tsx'
import css from './TaskPanel.module.css'

export interface TaskPanelProps {
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

export function TaskPanel({ tasks, activeTaskId, onSelect, onNewTask, onRename, onDelete }: TaskPanelProps) {
  const [query, setQuery] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q === '') return tasks
    return tasks.filter(
      (task) => task.title.toLowerCase().includes(q) || task.engagementId.toLowerCase().includes(q),
    )
  }, [tasks, query])

  const handleRename = (engagementId: string) => {
    const title = renameValue.trim()
    if (title) onRename(engagementId, title)
    setRenamingId(null)
    setRenameValue('')
  }

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>任务管理</span>
        <span className={css.spacer} />
        <button type="button" className={css.newBtn} onClick={onNewTask} title="新建任务">
          + 新建
        </button>
      </div>

      <div className={css.searchRow}>
        <input
          type="search"
          className={css.search}
          placeholder="搜索任务…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className={css.list}>
        {filtered.length === 0 ? (
          <div className={css.empty}>
            {query ? '无匹配任务——调整搜索。' : '暂无任务——点击右上角「+ 新建」或对话框直接输入目标开始。'}
          </div>
        ) : (
          filtered.map((task) => (
            <div
              key={task.engagementId}
              className={css.taskRow}
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
              <span className={css.slot}>
                <StateDot state={taskDotState(task.status)} size={10} />
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
                <span className={css.taskTitle} title={task.title}>{task.title}</span>
              )}

              {task.sessionCount > 0 && <span className={css.countBadge}>{task.sessionCount}</span>}
              <span className={css.time}>{formatClock(task.updatedAt)}</span>

              <span className={css.actions} onClick={(e) => e.stopPropagation()}>
                <button
                  type="button"
                  className={css.actionBtn}
                  title="重命名"
                  onClick={() => {
                    setRenamingId(task.engagementId)
                    setRenameValue(task.title)
                  }}
                >
                  ✎
                </button>
                {confirmDeleteId === task.engagementId ? (
                  <button
                    type="button"
                    className={css.confirmBtn}
                    title="确认删除"
                    onClick={() => {
                      onDelete(task.engagementId)
                      setConfirmDeleteId(null)
                    }}
                  >
                    ✓
                  </button>
                ) : (
                  <button
                    type="button"
                    className={css.actionBtn}
                    title="删除任务"
                    onClick={() => setConfirmDeleteId(task.engagementId)}
                  >
                    ✕
                  </button>
                )}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
