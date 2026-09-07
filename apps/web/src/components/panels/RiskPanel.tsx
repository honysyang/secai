/**
 * RiskPanel：风险管理视图（r6 用户视角重设计）。
 * 数据 = host/risks 帧 + bootstrap 拉取（AppSnapshot.risks）。
 * 结构 = 头部 + 严重度统计卡（点击筛选）+ 处理状态筛选 chips
 * + 风险行（severity 徽章 + 标题 + 目标 + 证据展开 + 状态流转 select）。
 * 状态流转走 runtime.updateRiskStatus（POST /api/updateRisk）。
 */

import { useMemo, useState } from 'react'
import type { RiskEntry } from '../../connection/api.ts'
import type { Severity } from '../../runtime/projections.ts'
import { formatDateTime } from '../../runtime/format.ts'
import { SEVERITY_LABEL } from '../details/severity.ts'
import sevCss from '../details/severity.module.css'
import { EmptyState } from '../primitives/EmptyState.tsx'
import css from './RiskPanel.module.css'

export interface RiskPanelProps {
  risks: readonly RiskEntry[]
  /** 状态流转回调（runtime.updateRiskStatus）。 */
  onStatusChange: (riskId: string, status: RiskEntry['status']) => Promise<void>
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

const STATUS_KEYS = Object.keys(STATUS_LABEL) as RiskEntry['status'][]

/** 风险行：severity 徽章 + 标题 + 目标 + 证据 + 状态流转。 */
function RiskRow({ risk, onStatusChange }: { risk: RiskEntry; onStatusChange: RiskPanelProps['onStatusChange'] }) {
  const [busy, setBusy] = useState(false)
  const evidence = risk.evidence ?? []
  return (
    <div className={css.row} data-sev={risk.severity}>
      <span className={sevCss.sev} data-sev={risk.severity}>
        {SEVERITY_LABEL[risk.severity]}
      </span>
      <div className={css.main}>
        <div className={css.rowTitle}>{risk.title}</div>
        <div className={css.meta}>
          {risk.target !== undefined && <span className={css.target}>{risk.target}</span>}
          <span className={css.time}>{formatDateTime(risk.discoveredAt)}</span>
        </div>
        {evidence.length > 0 && (
          <details className={css.evidence}>
            <summary>证据链（{evidence.length}）</summary>
            <ul className={css.evidenceList}>
              {evidence.map((item, index) => (
                <li key={index} className={css.evidenceItem}>{item}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
      <select
        className={css.statusSelect}
        value={risk.status}
        disabled={busy}
        aria-label="处理状态"
        onChange={async (e) => {
          const next = e.target.value as RiskEntry['status']
          setBusy(true)
          try {
            await onStatusChange(risk.id, next)
          } catch (cause) {
            window.alert(`状态更新失败：${cause instanceof Error ? cause.message : String(cause)}`)
          } finally {
            setBusy(false)
          }
        }}
      >
        {STATUS_KEYS.map((key) => (
          <option key={key} value={key}>{STATUS_LABEL[key]}</option>
        ))}
      </select>
    </div>
  )
}

export function RiskPanel({ risks, onStatusChange }: RiskPanelProps) {
  const [sevFilter, setSevFilter] = useState<Severity | 'all'>('all')
  const [statusFilter, setStatusFilter] = useState<RiskEntry['status'] | 'all'>('all')

  const severityCounts = useMemo(() => {
    const counts: Record<Severity, number> = { critical: 0, high: 0, medium: 0, low: 0, info: 0 }
    for (const risk of risks) counts[risk.severity] += 1
    return counts
  }, [risks])

  const openCount = useMemo(() => risks.filter((risk) => risk.status === 'open').length, [risks])

  const filtered = useMemo(
    () =>
      risks
        .filter((risk) => (sevFilter === 'all' ? true : risk.severity === sevFilter))
        .filter((risk) => (statusFilter === 'all' ? true : risk.status === statusFilter))
        .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)),
    [risks, sevFilter, statusFilter],
  )

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>风险清单</span>
        <span className={css.count}>{risks.length} 项 · 待处理 {openCount}</span>
      </div>

      {/* 严重度统计卡（点击筛选） */}
      <div className={css.sevCards}>
        {(['critical', 'high', 'medium', 'low', 'info'] as const).map((sev) => (
          <button
            key={sev}
            type="button"
            className={css.sevCard}
            data-sev={sev}
            data-active={sevFilter === sev || undefined}
            onClick={() => setSevFilter((current) => (current === sev ? 'all' : sev))}
          >
            <span className={css.sevCount}>{severityCounts[sev]}</span>
            <span className={css.sevLabel}>{SEVERITY_LABEL[sev]}</span>
          </button>
        ))}
        <span className={css.spacer} />
        <select
          className={css.statusFilter}
          value={statusFilter}
          aria-label="按处理状态筛选"
          onChange={(e) => setStatusFilter(e.target.value as RiskEntry['status'] | 'all')}
        >
          <option value="all">全部状态</option>
          {STATUS_KEYS.map((key) => (
            <option key={key} value={key}>{STATUS_LABEL[key]}</option>
          ))}
        </select>
      </div>

      <div className={css.list}>
        {risks.length === 0 ? (
          <EmptyState
            title="暂无风险"
            description="agent 确认的 finding 会按严重级别聚合在此（生成报告后自动落账），支持处理状态跟踪。"
          />
        ) : (
          filtered.map((risk) => (
            <RiskRow key={risk.id} risk={risk} onStatusChange={onStatusChange} />
          ))
        )}
        {risks.length > 0 && filtered.length === 0 && <div className={css.noMatch}>无匹配风险——调整筛选条件。</div>}
      </div>
    </div>
  )
}
