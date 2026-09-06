// App：顶层接线（dsh 复刻版主框架）。AppRuntime 为 module 级单例（start/stop
// 幂等，挂 window 供调试/联调探针），App 组件经 useSyncExternalStore 订阅
// 快照；顶部分发由 snapshot.route 驱动（工作台 = 对话面板；资产/风险/报告 =
// 独立管理视图），与选中会话解耦。用户动作（select/submitOrSend/respond/run）
// 回调 runtime。侧栏展开偏好 = App 层 state（sidebarOpen），「新建任务」弹窗
// 开关同层下传（AppFrame 负责窄屏 rail 几何，sidebarOpen 只管宽屏收展）。

import { useEffect, useState, useSyncExternalStore } from 'react'
import { AppFrame } from './components/layout/AppFrame.tsx'
import { SidebarPane } from './components/sidebar/SidebarPane.tsx'
import { ConversationView } from './components/conversation/ConversationView.tsx'
import { AssetPanel } from './components/panels/AssetPanel.tsx'
import { RiskPanel } from './components/panels/RiskPanel.tsx'
import { ReportPanel } from './components/panels/ReportPanel.tsx'
import { NewEngagementModal } from './components/engagement/NewEngagementModal.tsx'
import { AppRuntime } from './runtime/appRuntime.ts'
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

  // 生命周期：挂载即启动（幂等），卸载才回收（StrictMode 双挂载：
  // mount1 已由模块级 start 启动 → unmount stop → mount2 重新 start，
  // 保证 demo 泵在重挂载后继续投帧）。
  useEffect(() => {
    runtime.start()
    return () => runtime.stop()
  }, [])

  const snapshot = useSyncExternalStore(runtime.subscribe, runtime.snapshot, runtime.snapshot)
  const session = snapshot.engagement.sessions.find((item) => item.sessionId === snapshot.selectedId) ?? null

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
      sidebar={
        <SidebarPane
          engagement={snapshot.engagement}
          selectedId={snapshot.selectedId}
          tasks={snapshot.tasks}
          activeTaskId={snapshot.activeTaskId}
          link={snapshot.link}
          route={snapshot.route}
          onRoute={(route) => runtime.setRoute(route)}
          collapsed={!sidebarOpen}
          onToggle={() => setSidebarOpen((open) => !open)}
          onSelect={(sessionId) => runtime.select(sessionId)}
          onSelectTask={(engagementId) => runtime.selectTask(engagementId)}
        />
      }
      conversation={center}
    >
      <NewEngagementModal
        open={newEngagementOpen}
        onClose={() => setNewEngagementOpen(false)}
        onSubmit={async (brief: TaskBrief) => runtime.run(brief)}
      />
    </AppFrame>
  )
}
