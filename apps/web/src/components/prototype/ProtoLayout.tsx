// ProtoLayout：按 prototype.html 重做的 SECAI·PT 前端主壳。
//
// 顶层职责：
// 1. 单条导航侧栏（折叠/展开，Ctrl+B 切换；localStorage 记忆）
// 2. 顶部栏（搜索命令面板入口 + 主题切换 + 状态徽章）
// 3. 中间面板路由分发（工作台/资产/风险/报告/武器库/技能/知识库 + 7 个 view）
// 4. 命令面板 Ctrl+K + 1-7 快捷键切屏 + Esc 关闭
// 5. 任务目录 CRUD + 新建任务向导 + 任务调度 + 主题切换 + 键盘快捷键
//
// 数据层：localStorage 持久化的 proto-db；不依赖现有 AppRuntime 的 WS，
// 但保留外部可注入 onSubmit/onRespond 以便后续接入。

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import './proto.css'
import { loadDB, saveDB, DEFAULT_DB } from './db.ts'
import type { DBShape, Task, TaskStatus } from './db.ts'
import { ChatScreen } from './screens/ChatScreen.tsx'
import { AssetScreen } from './screens/AssetScreen.tsx'
import { RiskScreen } from './screens/RiskScreen.tsx'
import { ReportScreen } from './screens/ReportScreen.tsx'
import { ArsenalScreen } from './screens/ArsenalScreen.tsx'
import { SkillScreen } from './screens/SkillScreen.tsx'
import { KbScreen } from './screens/KbScreen.tsx'
import { CommandPalette } from './CommandPalette.tsx'
import { NewEngagementWizard } from './NewEngagementWizard.tsx'
import { ToastHost } from './Toast.tsx'

export type RouteKey = 'chat' | 'asset' | 'risk' | 'report' | 'arsenal' | 'skill' | 'kb'

const VIEW_NAMES: Record<RouteKey, string> = {
  chat: '工作台',
  asset: '资产',
  risk: '风险',
  report: '报告',
  arsenal: '武器库',
  skill: '技能',
  kb: '知识库',
}

const VIEW_ICONS: Record<RouteKey, ReactNode> = {
  chat: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.4c-1.4 0-2.8-.3-4-1L3 20l1.2-4.4A8.4 8.4 0 1 1 21 11.5z" />
    </svg>
  ),
  asset: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="7" rx="2" /><rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 7.5h.01M7 16.5h.01" />
    </svg>
  ),
  risk: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" />
    </svg>
  ),
  report: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M9 15h6M9 11h2" />
    </svg>
  ),
  arsenal: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><path d="M3.3 7 12 12l8.7-5M12 22V12" />
    </svg>
  ),
  skill: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="m13 2-2 9h6l-8 11 2-9H5l8-11z" />
    </svg>
  ),
  kb: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15z" /><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" />
    </svg>
  ),
}

const ORDER: RouteKey[] = ['chat', 'asset', 'risk', 'report', 'arsenal', 'skill', 'kb']
const KEYMAP: Record<string, RouteKey> = { '1': 'chat', '2': 'asset', '3': 'risk', '4': 'report', '5': 'arsenal', '6': 'skill', '7': 'kb' }

export interface ProtoLayoutProps {
  /** 初始任务目录数据（不传则用默认种子）。 */
  initialDB?: DBShape
}

export function ProtoLayout({ initialDB }: ProtoLayoutProps) {
  const [db, setDB] = useState<DBShape>(() => initialDB ?? loadDB())
  const [route, setRoute] = useState<RouteKey>('chat')
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof localStorage === 'undefined') return false
    return localStorage.getItem('secai-proto-collapsed') === '1'
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof localStorage === 'undefined') return 'light'
    const t = localStorage.getItem('secai-theme-mode')
    return t === 'dark' ? 'dark' : 'light'
  })

  // 同步 data-secai-dark（与现有 ThemeToggle 保持一致）
  useEffect(() => {
    if (theme === 'dark') document.body.setAttribute('data-secai-dark', '')
    else document.body.removeAttribute('data-secai-dark')
  }, [theme])

  // 持久化
  useEffect(() => {
    saveDB(db)
  }, [db])
  useEffect(() => {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem('secai-proto-collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  const currentTask = useMemo<Task | null>(() => {
    return db.tasks.find((t) => t.id === db.meta.curTaskId) ?? db.tasks[0] ?? null
  }, [db])

  const go = useCallback((id: RouteKey) => {
    setRoute(id)
    if (typeof window !== 'undefined' && window.innerWidth <= 1080) {
      setCollapsed(true)
    }
  }, [])

  // 全局快捷键
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const tag = target.tagName
      const isTextField = tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') {
        e.preventDefault()
        setCollapsed((c) => !c)
        return
      }
      if (e.key === 'Escape') {
        if (paletteOpen) {
          setPaletteOpen(false)
          return
        }
        if (newOpen) {
          setNewOpen(false)
          return
        }
        return
      }
      if (isTextField) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const k = KEYMAP[e.key]
      if (k) {
        e.preventDefault()
        go(k)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, paletteOpen, newOpen])

  // 增删任务
  const addTask = useCallback((cfg: { name: string; target: string; mode: Task['mode']; sched: Task['sched']; next: string }) => {
    const t: Task = {
      id: 't' + Date.now(),
      name: cfg.name,
      target: cfg.target,
      status: 'run',
      group: cfg.sched === 'now' ? '进行中' : '计划中 · 定时',
      mode: cfg.mode,
      sched: cfg.sched,
      next: cfg.next,
    }
    setDB((d) => {
      const next: DBShape = {
        ...d,
        tasks: [t, ...d.tasks],
        meta: { ...d.meta, curTaskId: t.id, curMode: t.mode },
      }
      return next
    })
    go('chat')
  }, [go])

  const selectTask = useCallback((id: string) => {
    const t = db.tasks.find((x) => x.id === id)
    if (!t) return
    setDB((d) => ({ ...d, meta: { ...d.meta, curTaskId: id, curMode: t.mode } }))
  }, [db])

  const deleteTask = useCallback((id: string) => {
    setDB((d) => {
      const tasks = d.tasks.filter((x) => x.id !== id)
      const curTaskId = d.meta.curTaskId === id ? (tasks[0]?.id ?? '') : d.meta.curTaskId
      return { ...d, tasks, meta: { ...d.meta, curTaskId } }
    })
  }, [])

  const renameTask = useCallback((id: string, name: string) => {
    setDB((d) => ({ ...d, tasks: d.tasks.map((t) => (t.id === id ? { ...t, name } : t)) }))
  }, [])

  // 命令面板命令
  const commands = useMemo(() => {
    const baseCommands: Array<{ id: string; g: string; t: string; k?: string; onRun: () => void }> = [
      ...ORDER.map((r, i) => ({ id: `nav-${r}`, g: '导航', t: `前往${VIEW_NAMES[r]}`, k: String(i + 1), onRun: () => go(r) })),
      { id: 'act-new', g: '操作', t: '新建渗透任务', k: 'N', onRun: () => setNewOpen(true) },
      { id: 'act-theme', g: '操作', t: '切换明暗主题', onRun: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')) },
      { id: 'act-report', g: '操作', t: '从当前任务生成报告', onRun: () => go('report') },
      { id: 'act-collapse', g: '操作', t: collapsed ? '展开导航' : '收起导航', onRun: () => setCollapsed((c) => !c) },
    ]
    const toolCommands = db.tasks.slice(0, 5).flatMap((t) => [
      { id: `tool-${t.id}-jump`, g: '任务', t: `打开任务：${t.name}`, onRun: () => { selectTask(t.id); go('chat') } },
    ])
    return [...baseCommands, ...toolCommands]
  }, [collapsed, db.tasks, selectTask, go])

  const target = currentTask?.target ?? '—'

  // 当前路由对应屏幕
  const screen =
    route === 'chat' ? (
      <ChatScreen db={db} currentTask={currentTask} onSelectTask={selectTask} onDeleteTask={deleteTask} onRenameTask={renameTask} onNewTask={() => setNewOpen(true)} />
    ) : route === 'asset' ? (
      <AssetScreen db={db} currentTask={currentTask} />
    ) : route === 'risk' ? (
      <RiskScreen db={db} currentTask={currentTask} />
    ) : route === 'report' ? (
      <ReportScreen db={db} currentTask={currentTask} />
    ) : route === 'arsenal' ? (
      <ArsenalScreen db={db} currentTask={currentTask} />
    ) : route === 'skill' ? (
      <SkillScreen db={db} currentTask={currentTask} />
    ) : (
      <KbScreen db={db} currentTask={currentTask} />
    )

  return (
    <div
      className="proto-shell"
      data-collapsed={collapsed ? 'true' : 'false'}
    >
      {/* 导航侧栏 */}
      <aside className="proto-nav">
        <div className="proto-nav-head">
          <div className="proto-logo" title="SECAI·PT 破阵">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M12 2.5 4 5.5v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10v-6l-8-3z" fill="currentColor" opacity=".28" />
              <path d="M12 2.5 4 5.5v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10v-6l-8-3z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
              <path d="M13.8 7.5 9.5 13h3l-1.3 4 4.5-5.8h-3l1.1-3.7z" fill="currentColor" />
            </svg>
          </div>
          {!collapsed && <span className="proto-brand">SECAI·PT 破阵</span>}
          <button
            className="proto-nav-toggle"
            type="button"
            title="展开 / 收起导航 (Ctrl+B)"
            aria-label="展开或收起导航菜单"
            onClick={() => setCollapsed((c) => !c)}
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M9 4v11M15 9h3M15 12h3" opacity=".5" />
            </svg>
          </button>
        </div>

        <nav className="proto-sb-nav" aria-label="主导航">
          {ORDER.map((r) => {
            const active = r === route
            return (
              <button
                key={r}
                type="button"
                className={'proto-sb-nav-item' + (active ? ' on' : '')}
                title={`${VIEW_NAMES[r]} (${ORDER.indexOf(r) + 1})`}
                onClick={() => go(r)}
              >
                {VIEW_ICONS[r]}
                {!collapsed && <span className="sb-nav-label">{VIEW_NAMES[r]}</span>}
                {!collapsed && <kbd>{ORDER.indexOf(r) + 1}</kbd>}
              </button>
            )
          })}
        </nav>

        {!collapsed && (
          <button type="button" className="proto-user" title="用户与权限">
            <span className="avatar">张</span>
            <span className="nu-t">
              <span className="nu-n">张伟</span>
              <span className="nu-r">管理员 · 全部权限</span>
            </span>
          </button>
        )}

        <div className="proto-nav-foot">
          <button
            className={'proto-icon-btn' + (theme === 'dark' ? ' on' : '')}
            type="button"
            title="切换明暗主题"
            onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          >
            {theme === 'dark' ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
              </svg>
            )}
          </button>
          <span className="proto-nav-status">
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--ok)', boxShadow: '0 0 0 3px rgba(48,164,108,.18)', animation: 'proto-pulse 1.6s infinite' }} />
            {!collapsed && <span className="proto-nav-status-txt">已连接 · 双通道</span>}
          </span>
        </div>
      </aside>
      <div className="proto-backdrop" onClick={() => setCollapsed(true)} />

      {/* Main */}
      <main className="proto-main">
        <div className="proto-topbar">
          <div className="proto-crumb">
            <b>{VIEW_NAMES[route]}</b>
            {route === 'chat' && <span className="mono" style={{ color: 'var(--text-3)', fontSize: 11 }}>· {target}</span>}
          </div>
          <span className="proto-spacer" />
          <button className="proto-pal-open" type="button" onClick={() => setPaletteOpen(true)}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <span>搜索命令、资产、工具…</span>
            <kbd>⌘K</kbd>
          </button>
        </div>

        <div className="proto-content">{screen}</div>
      </main>

      {/* 浮层 */}
      <CommandPalette
        open={paletteOpen}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
      />
      <NewEngagementWizard
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreate={(cfg) => {
          addTask(cfg)
          setNewOpen(false)
        }}
      />
      <ToastHost />
    </div>
  )
}

export { DEFAULT_DB }
export type { DBShape, Task, TaskStatus }