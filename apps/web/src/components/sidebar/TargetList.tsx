/**
 * TargetList：sidebar 目标列表（F3）。数据源 = host/session-* 帧登记出的
 * SessionSnapshot 列表（按 lastEventAt 倒序）；每行 = TargetItem + 子 agent
 * 缩进行。空态提示用于真实连接尚无会话时。
 */

import type { SessionSnapshot } from '../../runtime/session.ts'
import { deriveSubagents } from '../../runtime/eventTurns.ts'
import { TargetItem } from './TargetItem.tsx'
import css from './TargetList.module.css'

export interface TargetListProps {
  sessions: readonly SessionSnapshot[]
  selectedId: string | null
  onSelect: (sessionId: string) => void
}

export function TargetList({ sessions, selectedId, onSelect }: TargetListProps) {
  if (sessions.length === 0) {
    return (
      <div className={css.root}>
        <div className={css.empty}>暂无目标会话——提交任务书后 host/session-* 帧会在此登记目标行。</div>
      </div>
    )
  }
  return (
    <div className={css.root}>
      <div className={css.list} role="listbox" aria-label="目标会话列表">
        {sessions.map((session) => (
          <TargetItem
            key={session.sessionId}
            session={session}
            active={session.sessionId === selectedId}
            subagents={deriveSubagents(session.events)}
            onSelect={() => onSelect(session.sessionId)}
          />
        ))}
      </div>
    </div>
  )
}
