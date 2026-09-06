// App：顶层接线（复刻版主框架）。AppRuntime 为 module 级单例（start/stop
// 幂等，挂 window 供调试/联调探针），App 组件经 useSyncExternalStore 订阅
// 快照，把数据以 props 分发给三栏（SidebarPane / ConversationView /
// DetailsView）；用户动作（select/send/respond/run）回调 runtime。
// 侧栏展开偏好 = App 层 state（sidebarOpen），连同「新建任务」弹窗开关
// 一起下传（AppFrame 负责窄屏 rail 几何，sidebarOpen 只管宽屏收展）。

import { useEffect, useState, useSyncExternalStore } from 'react'
import { useThemeControl } from './components/theme/theme.ts'
import { AppFrame } from './components/layout/AppFrame.tsx'
import { SidebarPane } from './components/sidebar/SidebarPane.tsx'
import { ConversationView } from './components/conversation/ConversationView.tsx'
import { DetailsView } from './components/details/DetailsView.tsx'
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
  const theme = useThemeControl()
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [newEngagementOpen, setNewEngagementOpen] = useState(false)

  // StrictMode 开发态双挂载：卸载即回收首个 effect 的副作用（start/stop 幂等）
  useEffect(() => runtime.stop, [])

  const snapshot = useSyncExternalStore(runtime.subscribe, runtime.snapshot, runtime.snapshot)
  const session = snapshot.engagement.sessions.find((item) => item.sessionId === snapshot.selectedId) ?? null

  return (
    <AppFrame
      sidebarOpen={sidebarOpen}
      sidebar={
        <SidebarPane
          theme={theme}
          engagement={snapshot.engagement}
          selectedId={snapshot.selectedId}
          link={snapshot.link}
          collapsed={!sidebarOpen}
          onToggle={() => setSidebarOpen((open) => !open)}
          onSelect={(sessionId) => runtime.select(sessionId)}
          onNewEngagement={() => setNewEngagementOpen(true)}
        />
      }
      conversation={
        <ConversationView
          session={session}
          onSend={(text) => runtime.send(text)}
          onRespond={(rpcId, decision, comment) => runtime.respond(rpcId, decision, comment)}
        />
      }
      details={<DetailsView session={session} />}
    >
      <NewEngagementModal
        open={newEngagementOpen}
        onClose={() => setNewEngagementOpen(false)}
        onSubmit={async (brief: TaskBrief) => runtime.run(brief)}
      />
    </AppFrame>
  )
}
