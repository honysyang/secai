// ProtoLayout：按 prototype.html 重做的 SECAI·PT 前端主壳。
//
// 顶层职责：
// 1. 单条导航侧栏（折叠/展开，Ctrl+B 切换；localStorage 记忆）
// 2. 顶部栏（搜索命令面板入口 + 设置图标 + 状态徽章）
// 3. 中间面板路由分发（工作台/资产/风险/报告/武器库/技能/知识库 + 7 个 view）
// 4. 命令面板 Ctrl+K + 1-7 快捷键切屏 + Esc 关闭
// 5. 任务目录 CRUD + 新建任务向导 + 任务调度 + 主题切换 + 键盘快捷键
// 6. 设置中心 5 tab + 作战模式 6 种 + 任务调度（即时/定时/周期）
//
// 数据层：localStorage 持久化的 proto-db；不依赖现有 AppRuntime 的 WS。

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import './proto.css'
import { ProtoRuntime } from './ProtoRuntime.ts'
import type { Task } from './db.ts'
import { DEFAULT_DB } from './db.ts'
import type { DBShape } from './db.ts'
import { ChatScreen } from './screens/ChatScreen.tsx'
import { AssetScreen } from './screens/AssetScreen.tsx'
import { RiskScreen } from './screens/RiskScreen.tsx'
import { ReportScreen } from './screens/ReportScreen.tsx'
import { ArsenalScreen } from './screens/ArsenalScreen.tsx'
import { SkillScreen } from './screens/SkillScreen.tsx'
import { KbScreen } from './screens/KbScreen.tsx'
import { CommandPalette } from './CommandPalette.tsx'
import { NewEngagementWizard } from './NewEngagementWizard.tsx'
import { ToastHost, showToast } from './Toast.tsx'
import { SettingsModal, type SettingsTab } from './SettingsModal.tsx'
import { ModeModal, type CombatMode } from './ModeModal.tsx'
import { SchedModal, type SchedType } from './SchedModal.tsx'

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
  /** 注入的数据源（默认新建内置实例）；传入后由外部驱动 WS 快照。 */
  proto?: ProtoRuntime
}

let sharedProto: ProtoRuntime | null = null

/** module 级单例数据源（StrictMode 双挂载安全）。 */
function defaultProto(): ProtoRuntime {
  if (sharedProto === null) {
    // 惰性取 AppRuntime 单例（App.tsx 已 start）
    const runtime = (window as unknown as { __secai?: import('../../runtime/appRuntime.ts').AppRuntime }).__secai
    if (runtime === undefined) {
      throw new Error('ProtoLayout 需要 AppRuntime 单例（window.__secai），请先渲染 App.tsx 顶层。')
    }
    sharedProto = new ProtoRuntime(runtime)
  }
  return sharedProto
}

export function ProtoLayout({ proto }: ProtoLayoutProps) {
  const dataRef = useMemo(() => proto ?? defaultProto(), [proto])
  const db = useSyncExternalStore(dataRef.subscribe, dataRef.snapshot, dataRef.snapshot)

  // 数据面活性兜底：module 顶层 start 在 StrictMode 下会被 effect 清理停掉
  // （旧 UI 的 LegacyApp effect 在 proto 分支不跑），此处补幂等 start 保活。
  useEffect(() => {
    dataRef.ensureRunning()
  }, [dataRef])
  const [route, setRoute] = useState<RouteKey>('chat')
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof localStorage === 'undefined') return false
    return localStorage.getItem('secai-proto-collapsed') === '1'
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('model')
  const [modeOpen, setModeOpen] = useState(false)
  const [schedOpen, setSchedOpen] = useState<{ taskId: string } | null>(null)
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

  // 折叠态持久化（db 已由 ProtoRuntime 经 WS 驱动，不再本地持久化）
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

  const currentMode: CombatMode = (db.meta.curMode as CombatMode) ?? 'pt'

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
        if (paletteOpen) { setPaletteOpen(false); return }
        if (newOpen) { setNewOpen(false); return }
        if (settingsOpen) { setSettingsOpen(false); return }
        if (modeOpen) { setModeOpen(false); return }
        if (schedOpen) { setSchedOpen(null); return }
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
  }, [go, paletteOpen, newOpen, settingsOpen, modeOpen, schedOpen])

  // 增删任务：全部走真实 RPC（ProtoRuntime）
  const addTask = useCallback((cfg: { name: string; target: string; mode: Task['mode']; sched: Task['sched']; next: string }) => {
    void dataRef.createTask(cfg).then(() => {
      showToast('任务已下发 · 会话创建中', 'ok')
    }).catch((cause) => {
      const msg = cause instanceof Error ? cause.message : String(cause)
      showToast(`任务创建失败：${msg}`, 'err')
    })
    go('chat')
  }, [dataRef, go])

  const selectTask = useCallback((id: string) => {
    dataRef.selectTask(id)
  }, [dataRef])

  const deleteTask = useCallback((id: string) => {
    void dataRef.deleteTask(id).catch((cause) => {
      const msg = cause instanceof Error ? cause.message : String(cause)
      showToast(`删除失败：${msg}`, 'err')
    })
  }, [dataRef])

  const renameTask = useCallback((id: string, name: string) => {
    void dataRef.renameTask(id, name).catch((cause) => {
      const msg = cause instanceof Error ? cause.message : String(cause)
      showToast(`重命名失败：${msg}`, 'err')
    })
  }, [dataRef])

  const applySettings = useCallback((p: {
    settings?: Partial<DBShape['settings']>
    rules?: DBShape['rules']
    members?: DBShape['members']
    apis?: DBShape['apis']
  }) => {
    // 设置中心为本地展示层（endpoint/model/审批规则等），后端无对应 RPC；仅回执提示。
    void p
    showToast('设置已保存 · 新任务生效，进行中的任务不受影响', 'ok')
  }, [])

  const applyMode = useCallback((m: CombatMode) => {
    const curId = db.meta.curTaskId
    if (curId) dataRef.applyMode(curId, m)
  }, [dataRef, db.meta.curTaskId])

  const openSched = useCallback((taskId?: string) => {
    const id = taskId ?? db.meta.curTaskId ?? db.tasks[0]?.id
    if (id !== undefined) setSchedOpen({ taskId: id })
  }, [db.meta.curTaskId, db.tasks])
  const saveSched = useCallback((s: { sched: SchedType; next: string }) => {
    if (!schedOpen) return
    void dataRef.saveSched(schedOpen.taskId, s)
    showToast('调度已保存', 'ok')
    setSchedOpen(null)
  }, [dataRef, schedOpen])

  // 命令面板命令
  const commands = useMemo(() => {
    const NAV_ICONS: Record<RouteKey, string> = {
      chat: 'M3 12h4l3-9 4 18 3-9h4',
      asset: 'M3 5h7v7H3zM14 5h7v7h-7zM3 14h7v5H3zM14 14h7v5h-7z',
      risk: 'M12 2 4 7v10l8 5 8-5V7z',
      report: 'M4 4h16v6H4zM4 14h16v6H4z',
      arsenal: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z',
      skill: 'M12 2l9 5v10l-9 5-9-5V7z',
      kb: 'M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z',
    }
    const baseCommands: Array<{ id: string; g: string; t: string; k?: string; icon?: string; onRun: () => void }> = [
      ...ORDER.map((r, i) => ({ id: `nav-${r}`, g: '导航', t: `前往${VIEW_NAMES[r]}`, k: String(i + 1), icon: NAV_ICONS[r], onRun: () => go(r) })),
      { id: 'act-new', g: '操作', t: '新建渗透任务', k: 'N', icon: 'M12 2v20M2 12h20', onRun: () => setNewOpen(true) },
      { id: 'act-mode', g: '操作', t: '切换作战模式', icon: 'M12 2 4 7v10l8 5 8-5V7z', onRun: () => setModeOpen(true) },
      { id: 'act-report', g: '操作', t: '从当前任务生成报告', icon: 'M4 4h16v6H4zM4 14h16v6H4z', onRun: () => go('report') },
      { id: 'act-collapse', g: '操作', t: collapsed ? '展开导航' : '收起导航', icon: 'M3 6h18M3 12h18M3 18h18', onRun: () => setCollapsed((c) => !c) },
      { id: 'act-theme', g: '操作', t: '切换明暗主题', icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20', onRun: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')) },
      { id: 'act-set-model', g: '设置', t: '设置 · 模型', icon: 'M12 2a10 10 0 1 0 0 20', onRun: () => { setSettingsTab('model'); setSettingsOpen(true) } },
      { id: 'act-set-approval', g: '设置', t: '设置 · 审批策略', icon: 'M5 11V7a5 5 0 0 1 10 0v4M3 11h14v10H3z', onRun: () => { setSettingsTab('approval'); setSettingsOpen(true) } },
      { id: 'act-set-sched', g: '设置', t: '设置 · 调度', icon: 'M3 6h18M3 12h18M3 18h18', onRun: () => { setSettingsTab('sched'); setSettingsOpen(true) } },
      { id: 'act-set-users', g: '设置', t: '设置 · 用户与权限', icon: 'M12 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM4 22v-3a8 8 0 0 1 16 0v3', onRun: () => { setSettingsTab('users'); setSettingsOpen(true) } },
      { id: 'act-set-general', g: '设置', t: '设置 · 通用', icon: 'M3 4h18v6H3zM3 14h18v6H3z', onRun: () => { setSettingsTab('general'); setSettingsOpen(true) } },
    ]
    const toolCommands = db.tasks.slice(0, 5).flatMap((t) => [
      { id: `tool-${t.id}-jump`, g: '任务', t: `打开任务：${t.name}`, icon: 'M3 12h4l3-9 4 18 3-9h4', onRun: () => { selectTask(t.id); go('chat') } },
      { id: `tool-${t.id}-sched`, g: '任务', t: `设置定时：${t.name}`, icon: 'M3 6h18M3 12h18M3 18h18', onRun: () => openSched(t.id) },
    ])
    return [...baseCommands, ...toolCommands]
  }, [collapsed, db.tasks, selectTask, go, openSched])

  const target = currentTask?.target ?? '—'

  // 当前路由对应屏幕
  const screen =
    route === 'chat' ? (
      <ChatScreen
        db={db}
        proto={dataRef}
        currentTask={currentTask}
        currentMode={currentMode}
        onSelectTask={selectTask}
        onDeleteTask={deleteTask}
        onRenameTask={renameTask}
        onNewTask={() => setNewOpen(true)}
        onOpenSched={openSched}
        onOpenMode={() => setModeOpen(true)}
        onOpenSettings={() => { setSettingsTab('model'); setSettingsOpen(true) }}
      />
    ) : route === 'asset' ? (
      <AssetScreen db={db} currentTask={currentTask} />
    ) : route === 'risk' ? (
      <RiskScreen db={db} currentTask={currentTask} onToast={showToast} onUpdateStatus={(id, st) => { dataRef.updateRiskStatus(id, st).then(() => showToast(`风险状态已更新：${st}`, 'ok')).catch((e) => showToast(`更新失败：${e.message}`, 'err')) }} />
    ) : route === 'report' ? (
      <ReportScreen db={db} currentTask={currentTask} onToast={showToast} onExport={() => { if (!currentTask) return; dataRef.generateReport(currentTask.id).then(() => showToast('报告已生成 · 产物列表已刷新', 'ok')).catch((e) => showToast(`报告生成失败：${e.message}`, 'err')) }} />
    ) : route === 'arsenal' ? (
      <ArsenalScreen db={db} currentTask={currentTask} />
    ) : route === 'skill' ? (
      <SkillScreen db={db} currentTask={currentTask} onPillActivate={() => { setRoute('chat') }} />
    ) : (
      <KbScreen db={db} currentTask={currentTask} onToast={showToast} />
    )

  const schedTargetTask = schedOpen ? db.tasks.find((t) => t.id === schedOpen.taskId) ?? null : null

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
          <button type="button" className="proto-user" title="用户与权限" onClick={() => { setSettingsTab('users'); setSettingsOpen(true) }}>
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
          <button
            className="proto-icon-btn"
            type="button"
            title="设置"
            onClick={() => { setSettingsTab('model'); setSettingsOpen(true) }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h.1a1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
            </svg>
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
      <SettingsModal
        open={settingsOpen}
        initialTab={settingsTab}
        db={db}
        onClose={() => setSettingsOpen(false)}
        onSave={applySettings}
      />
      <ModeModal
        open={modeOpen}
        current={currentMode}
        onClose={() => setModeOpen(false)}
        onApply={(m) => { applyMode(m); showToast('已应用推荐模型 · 仅本任务', 'ok') }}
      />
      <SchedModal
        open={schedOpen !== null}
        task={schedTargetTask}
        onClose={() => setSchedOpen(null)}
        onSave={saveSched}
      />
      <ToastHost />
    </div>
  )
}

export { DEFAULT_DB }
export type { DBShape, Task }