// App：顶层接线（dsh 复刻版主框架）。AppRuntime 为 module 级单例（start/stop
// 幂等，挂 window 供调试/联调探针），App 组件经 useSyncExternalStore 订阅
// 快照；顶部分发由 snapshot.route 驱动（工作台 = 对话面板；资产/风险/报告 =
// 独立管理视图），与选中会话解耦。用户动作（select/submitOrSend/respond/run）
// 回调 runtime。侧栏展开偏好 = App 层 state（sidebarOpen），「新建任务」弹窗
// 开关同层下传（AppFrame 负责窄屏 rail 几何，sidebarOpen 只管宽屏收展）。

import { useEffect, useState, useSyncExternalStore } from 'react'
import { AppFrame, HEADER_HEIGHT } from './components/layout/AppFrame.tsx'
import { SidebarPane } from './components/sidebar/SidebarPane.tsx'
import { ConversationView } from './components/conversation/ConversationView.tsx'
import { AssetPanel } from './components/panels/AssetPanel.tsx'
import { RiskPanel } from './components/panels/RiskPanel.tsx'
import { ReportPanel } from './components/panels/ReportPanel.tsx'
import { ArsenalPanel } from './components/panels/ArsenalPanel.tsx'
import { SkillsPanel } from './components/panels/SkillsPanel.tsx'
import { KnowledgePanel } from './components/panels/KnowledgePanel.tsx'
import { NewEngagementWizard } from './components/engagement/NewEngagementWizard.tsx'
import { ToastHost } from './components/primitives/Toast.tsx'
import { CommandPalette } from './components/primitives/CommandPalette.tsx'
import type { Command } from './components/primitives/CommandPalette.tsx'
import { ThemeToggle } from './components/primitives/ThemeToggle.tsx'
import { StateDot } from './components/primitives/StateDot.tsx'
import { AppRuntime } from './runtime/appRuntime.ts'
import { LoginModal } from './components/auth/LoginModal.tsx'
import { onUnauthorized } from './auth/apiKeyStore.ts'
import type { TaskBrief } from './connection/api.ts'

/** module 级单例：数据源与调度全局只此一份（StrictMode 双挂载安全）。 */
const runtime = new AppRuntime()
runtime.start()
if (typeof window !== 'undefined') {
  // 联调探针：控制台可直查快照（api 暴露见 appRuntime.ts）
  ;(window as unknown as { __secai?: AppRuntime }).__secai = runtime
}

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [newEngagementOpen, setNewEngagementOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)

  // 生命周期：挂载即启动（幂等），卸载才回收（StrictMode 双挂载：
  // mount1 已由模块级 start 启动 → unmount stop → mount2 重新 start，
  // 保证 demo 泵在重挂载后继续投帧）。
  useEffect(() => {
    runtime.start()
    return () => runtime.stop()
  }, [])

  // 监听 CommandPalette 的全局打开事件
  useEffect(() => {
    const onOpen = () => setPaletteOpen(true)
    window.addEventListener('secai:open-palette', onOpen)
    return () => window.removeEventListener('secai:open-palette', onOpen)
  }, [])

  // 监听未授权事件 → 弹出登录
  useEffect(() => {
    return onUnauthorized(() => setLoginOpen(true))
  }, [])

  const snapshot = useSyncExternalStore(runtime.subscribe, runtime.snapshot, runtime.snapshot)
  const session = snapshot.engagement.sessions.find((item) => item.sessionId === snapshot.selectedId) ?? null

  // 构建命令面板命令列表
  const commands: Command[] = [
    // 导航
    { id: 'nav-workbench', label: '工作台', hint: '导航', group: '导航', keywords: ['workbench', '对话'], onRun: () => runtime.setRoute('workbench') },
    { id: 'nav-assets', label: '资产', hint: '导航', group: '导航', keywords: ['assets', 'asset'], onRun: () => runtime.setRoute('assets') },
    { id: 'nav-risks', label: '风险', hint: '导航', group: '导航', keywords: ['risks', 'risk'], onRun: () => runtime.setRoute('risks') },
    { id: 'nav-reports', label: '报告', hint: '导航', group: '导航', keywords: ['reports', 'report'], onRun: () => runtime.setRoute('reports') },
    { id: 'nav-arsenal', label: '武器库', hint: '导航', group: '导航', keywords: ['arsenal', 'tools'], onRun: () => runtime.setRoute('arsenal') },
    { id: 'nav-skills', label: 'Skills', hint: '导航', group: '导航', keywords: ['skills', 'skill'], onRun: () => runtime.setRoute('skills') },
    { id: 'nav-knowledge', label: '知识库', hint: '导航', group: '导航', keywords: ['knowledge'], onRun: () => runtime.setRoute('knowledge') },
    // 任务
    { id: 'task-new', label: '新建任务', hint: '任务', group: '任务', keywords: ['new', 'engagement', 'run'], onRun: () => setNewEngagementOpen(true) },
    // 工具（从武器库动态生成）
    ...snapshot.tools.slice(0, 10).map((tool) => ({
      id: `tool-${tool.name}`,
      label: `使用 ${tool.name}`,
      hint: tool.shortDescription,
      group: '工具' as const,
      keywords: [tool.name, tool.shortDescription],
      onRun: () => {
        runtime.setRoute('workbench')
        // TODO: 预填输入框
      },
    })),
  ]

  // 路由分发：工作台对话面板 / 资产管理 / 风险管理 / 报告管理
  const center =
    snapshot.route === 'assets' ? (
      <AssetPanel assets={snapshot.assets} />
    ) : snapshot.route === 'risks' ? (
      <RiskPanel
        risks={snapshot.risks}
        onStatusChange={(riskId, status) => runtime.updateRiskStatus(riskId, status)}
      />
    ) : snapshot.route === 'reports' ? (
      <ReportPanel
        reports={snapshot.reports}
        onGenerate={(engagementId) => runtime.generateReport(engagementId)}
      />
    ) : snapshot.route === 'arsenal' ? (
      <ArsenalPanel tools={snapshot.tools} />
    ) : snapshot.route === 'skills' ? (
      <SkillsPanel skills={snapshot.skills} />
    ) : snapshot.route === 'knowledge' ? (
      <KnowledgePanel knowledge={snapshot.knowledge} />
    ) : (
      <ConversationView
        session={session}
        onSubmit={(text) => runtime.submitOrSend(text)}
        onRespond={(rpcId, decision, comment) => runtime.respond(rpcId, decision, comment)}
        onNewTask={() => setNewEngagementOpen(true)}
      />
    )

  return (
    <AppFrame
      sidebarOpen={sidebarOpen}
      header={
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 20px', height: HEADER_HEIGHT, fontSize: 13, color: 'var(--secai-alias-label-secondary)' }}>
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: 320,
              height: 30,
              padding: '0 12px',
              border: '1px solid var(--secai-alias-border-l2)',
              borderRadius: 8,
              background: 'var(--secai-alias-bg-layer-1)',
              color: 'var(--secai-alias-label-tertiary)',
              fontSize: 12,
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
              <circle cx="6" cy="6" r="4" />
              <path d="M9 9l3 3" />
            </svg>
            <span style={{ flex: 1 }}>搜索命令、资产、工具…</span>
            <kbd style={{ padding: '1px 4px', borderRadius: 3, background: 'var(--secai-alias-bg-layer-2)', fontSize: 10, fontFamily: 'inherit' }}>⌘K</kbd>
          </button>
          <span style={{ flex: 1 }} />
          <StateDot state={snapshot.link === 'connected' ? 'done' : 'warning'} size={8} />
          <ThemeToggle />
          <span style={{ fontSize: 11, color: 'var(--secai-alias-label-caption)', fontWeight: 500 }}>v4.0</span>
        </div>
      }
      sidebar={
        <SidebarPane
          engagement={snapshot.engagement}
          selectedId={snapshot.selectedId}
          link={snapshot.link}
          route={snapshot.route}
          onRoute={(route) => runtime.setRoute(route)}
          collapsed={!sidebarOpen}
          onToggle={() => setSidebarOpen((open) => !open)}
          onSelect={(sessionId) => runtime.select(sessionId)}
        />
      }
      conversation={center}
      workbench={snapshot.route === 'workbench'}
    >
      <NewEngagementWizard
        open={newEngagementOpen}
        onClose={() => setNewEngagementOpen(false)}
        onSubmit={async (brief: TaskBrief) => runtime.run(brief)}
      />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
      <LoginModal
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        onSuccess={() => { runtime.start(); }}
      />
      <ToastHost />
    </AppFrame>
  )
}
