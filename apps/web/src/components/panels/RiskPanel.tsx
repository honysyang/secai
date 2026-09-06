/**
 * RiskPanel：风险管理视图。数据 = host/risks 帧（AppSnapshot.risks）。
 * 按严重级别分组呈现 confirmed findings：severity 徽章 + 标题 + 目标 + 状态 + 发现时间。
 */

import type { RiskEntry } from '../../connection/api.ts'
import type { Severity } from '../../runtime/projections.ts'
import { formatDateTime } from '../../runtime/format.ts'
import { SEVERITY_LABEL } from '../details/severity.ts'
import sevCss from '../details/severity.module.css'
import css from './RiskPanel.module.css'

export interface RiskPanelProps {
  risks: readonly RiskEntry[]
}

/** 严重级别排序权重（critical 最前）。 */
const SEVERITY_ORDER: Record<Severity, number> = {
  critical: 0, high: 1, medium: 2, low: 3, info: 4,
}

/** 处理状态中文标签。 */
const STATUS_LABEL: Record<RiskEntry['status'], string> = {
  open: '待处理',
  mitigating: '缓解中',
  accepted: '已接受',
  resolved: '已解决',
}

function RiskRow({ risk }: { risk: RiskEntry }) {
  return (
    <div className={css.row}>
      <span className={sevCss.sev} data-sev={risk.severity}>
        {SEVERITY_LABEL[risk.severity]}
      </span>
      <div className={css.main}>
        <div className={css.title}>{risk.title}</div>
        <div className={css.meta}>
          {risk.target !== undefined && <span className={css.target}>{risk.target}</span>}
          <span className={css.status} data-status={risk.status}>
            {STATUS_LABEL[risk.status]}
          </span>
          <span className={css.time}>{formatDateTime(risk.discoveredAt)}</span>
        </div>
      </div>
    </div>
  )
}

export function RiskPanel({ risks }: RiskPanelProps) {
  if (risks.length === 0) {
    return (
      <div className={css.empty}>
        暂无风险——agent 确认的 finding 会按严重级别聚合在此，支持处理状态跟踪。
      </div>
    )
  }
  const sorted = [...risks].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99),
  )
  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>风险清单</span>
        <span className={css.count}>{risks.length} 项</span>
      </div>
      <div className={css.list}>
        {sorted.map((risk) => (
          <RiskRow key={risk.id} risk={risk} />
        ))}
      </div>
    </div>
  )
}
