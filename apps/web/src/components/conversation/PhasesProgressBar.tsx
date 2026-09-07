/** PhasesProgressBar：轻量渗透阶段进度条（顶栏下方）。 */

import type { TimelineTurn } from '../../runtime/eventTurns.ts'
import css from './PhasesProgressBar.module.css'

export interface PhasesProgressBarProps {
  turns: readonly TimelineTurn[]
}

/** 阶段定义（按常见渗透流程）。 */
const PHASES = [
  { key: 'recon', label: '侦察' },
  { key: 'scan', label: '扫描' },
  { key: 'exploit', label: '利用' },
  { key: 'post', label: '后渗透' },
  { key: 'report', label: '报告' },
]

/** 从事件中推断当前阶段。 */
function inferPhase(turns: readonly TimelineTurn[]): number {
  let maxIdx = -1
  for (const turn of turns) {
    if (turn.kind === 'tool') {
      const name = turn.name.toLowerCase()
      if (name.includes('nmap') || name.includes('port') || name.includes('scan')) {
        maxIdx = Math.max(maxIdx, 1)
      } else if (name.includes('exploit') || name.includes('cve') || name.includes('msf')) {
        maxIdx = Math.max(maxIdx, 2)
      } else if (name.includes('privesc') || name.includes('lateral') || name.includes('hashdump')) {
        maxIdx = Math.max(maxIdx, 3)
      } else if (name.includes('report') || name.includes('doc')) {
        maxIdx = Math.max(maxIdx, 4)
      } else {
        maxIdx = Math.max(maxIdx, 0)
      }
    }
  }
  return maxIdx
}

export function PhasesProgressBar({ turns }: PhasesProgressBarProps) {
  const currentPhase = inferPhase(turns)

  if (currentPhase < 0) return null

  return (
    <div className={css.root} role="progressbar" aria-valuenow={currentPhase + 1} aria-valuemin={0} aria-valuemax={PHASES.length}>
      {PHASES.map((phase, i) => (
        <div key={phase.key} className={css.phase}>
          <div className={`${css.bar} ${i <= currentPhase ? css.done : ''} ${i === currentPhase ? css.active : ''}`} />
          <span className={`${css.label} ${i <= currentPhase ? css.doneLabel : ''}`}>{phase.label}</span>
        </div>
      ))}
    </div>
  )
}
