/**
 * App：顶层接线（F2/F3 集成）。主题控制 + AppRuntime（DEMO_MODE 开关见
 * runtime/appRuntime.ts）经 useSyncExternalStore 订阅快照，把 Session/
 * Engagement 数据以 props 分发给三栏内容（SidebarPane / ConversationView /
 * DetailsView）；用户动作（select/send/respond）回调 runtime。
 */

import { useEffect, useState, useSyncExternalStore } from 'react'
import { useThemeControl } from './components/theme/theme.ts'
import { AppFrame } from './components/layout/AppFrame.tsx'
import { SidebarPane } from './components/sidebar/SidebarPane.tsx'
import { ConversationView } from './components/conversation/ConversationView.tsx'
import { DetailsView } from './components/details/DetailsView.tsx'
import { AppRuntime } from './runtime/appRuntime.ts'
import type { LinkState } from './runtime/appRuntime.ts'

function linkPresentation(link: LinkState): { label: string; tone: 'demo' | 'live' | 'offline' } {
  switch (link) {
    case 'demo':
      return { label: 'DEMO', tone: 'demo' }
    case 'connected':
      return { label: '已连接', tone: 'live' }
    case 'connecting':
      return { label: '连接中', tone: 'offline' }
    case 'reconnecting':
      return { label: '重连中', tone: 'offline' }
  }
}

export default function App() {
  const theme = useThemeControl()
  const [runtime] = useState(() => {
    const created = new AppRuntime()
    created.start()
    return created
  })
  // StrictMode 开发态双挂载：卸载即回收首个实例的 demo 定时器
  useEffect(() => () => runtime.stop(), [runtime])

  const snapshot = useSyncExternalStore(runtime.subscribe, runtime.snapshot, runtime.snapshot)
  const session = snapshot.engagement.sessions.find((item) => item.sessionId === snapshot.selectedId) ?? null
  const link = linkPresentation(snapshot.link)

  return (
    <AppFrame
      sidebar={
        <SidebarPane
          theme={theme}
          engagement={snapshot.engagement}
          selectedId={snapshot.selectedId}
          onSelect={(sessionId) => runtime.select(sessionId)}
        />
      }
      conversation={
        <ConversationView
          theme={theme}
          linkLabel={link.label}
          linkTone={link.tone}
          session={session}
          onSend={(text) => runtime.send(text)}
          onRespond={(rpcId, decision, comment) => runtime.respond(rpcId, decision, comment)}
        />
      }
      details={<DetailsView session={session} />}
    />
  )
}
