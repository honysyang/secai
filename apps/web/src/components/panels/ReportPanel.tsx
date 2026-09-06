/**
 * ReportPanel：报告中心视图（r6 用户视角重设计）。
 * 数据 = host/reports 帧 + bootstrap 拉取（AppSnapshot.reports）。
 * 结构 = 头部（标题 + 计数 + 搜索）+ 状态筛选 chips + 报告卡片网格
 * （状态徽章 + 发现统计 + 严重度分布 + 导出三格式 + 生成/重新生成）。
 * 生成走 runtime.generateReport（POST /api/report，风险账本服务端联动）。
 */

import { useMemo, useState } from 'react'
import type { ReportEntry } from '../../connection/api.ts'
import { downloadReport } from '../../connection/api.ts'
import { formatDateTime } from '../../runtime/format.ts'
import { Button } from '../primitives/Button.tsx'
import css from './ReportPanel.module.css'

export interface ReportPanelProps {
  reports: readonly ReportEntry[]
  /** 生成/重新生成报告（runtime.generateReport）。 */
  onGenerate: (engagementId: string) => Promise<void>
}

/** 状态筛选档位。 */
type StatusFilter = 'all' | 'ready' | 'drafting'

const STATUS_LABEL: Record<ReportEntry['status'], string> = {
  ready: '已就绪',
  drafting: '撰写中',
}

/** 触发报告导出下载（md/json/pdf 三格式）。 */
async function exportReport(engagementId: string, format: 'md' | 'json' | 'pdf'): Promise<void> {
  try {
    await downloadReport(engagementId, format)
  } catch (cause) {
    window.alert(`导出报告失败：${cause instanceof Error ? cause.message : String(cause)}`)
  }
}

/** 报告卡片：标题 + 状态 + 元信息 + 严重度分布 + 导出与生成动作。 */
function ReportCard({ report, onGenerate }: { report: ReportEntry; onGenerate: ReportPanelProps['onGenerate'] }) {
  const [busy, setBusy] = useState(false)
  const sevCounts = Object.entries(report.severityCounts ?? {}).filter(([, count]) => count > 0)
  return (
    <div className={css.card} data-status={report.status}>
      <div className={css.cardHead}>
        <span className={css.cardTitle} title={report.title}>{report.title}</span>
        <span className={css.status} data-status={report.status}>
          {STATUS_LABEL[report.status]}
        </span>
      </div>
      <div className={css.cardMeta}>
        {report.generatedAt !== undefined && (
          <span>生成于 {formatDateTime(report.generatedAt)}</span>
        )}
        <span className={css.findings}>发现 {report.findingsCount} 项</span>
      </div>
      {sevCounts.length > 0 && (
        <div className={css.sevCounts}>
          {sevCounts.map(([sev, count]) => (
            <span key={sev} className={css.sevCount} data-sev={sev}>
              {sev} {count}
            </span>
          ))}
        </div>
      )}
      <div className={css.cardActions}>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await onGenerate(report.engagementId)
            } catch (cause) {
              window.alert(`生成报告失败：${cause instanceof Error ? cause.message : String(cause)}`)
            } finally {
              setBusy(false)
            }
          }}
        >
          {report.status === 'ready' ? '重新生成' : '生成报告'}
        </Button>
        {report.status === 'ready' && (
          <>
            <Button size="sm" variant="ghost" onClick={() => void exportReport(report.engagementId, 'md')}>
              Markdown
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void exportReport(report.engagementId, 'pdf')}>
              PDF
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void exportReport(report.engagementId, 'json')}>
              JSON
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

export function ReportPanel({ reports, onGenerate }: ReportPanelProps) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')

  const readyCount = useMemo(() => reports.filter((report) => report.status === 'ready').length, [reports])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return reports.filter((report) => {
      if (status !== 'all' && report.status !== status) return false
      if (q === '') return true
      return report.title.toLowerCase().includes(q) || report.engagementId.toLowerCase().includes(q)
    })
  }, [reports, status, query])

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>报告中心</span>
        <span className={css.count}>{reports.length} 份 · 已就绪 {readyCount}</span>
        <span className={css.spacer} />
        <input
          className={css.search}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索报告标题 / 任务…"
          aria-label="搜索报告"
        />
      </div>

      <div className={css.filters}>
        {(['all', 'ready', 'drafting'] as const).map((key) => {
          const count = key === 'all' ? reports.length : reports.filter((report) => report.status === key).length
          return (
            <button
              key={key}
              type="button"
              className={css.chip}
              data-active={status === key || undefined}
              onClick={() => setStatus(key)}
            >
              {key === 'all' ? '全部' : STATUS_LABEL[key]} {count}
            </button>
          )
        })}
      </div>

      {reports.length === 0 ? (
        <div className={css.empty}>
          暂无报告——agent 完成渗透测试后在此生成可交付的 Markdown / PDF / JSON 报告；也可在任务会话中下达「生成报告」指令。
        </div>
      ) : (
        <div className={css.grid}>
          {filtered.map((report) => (
            <ReportCard key={report.id} report={report} onGenerate={onGenerate} />
          ))}
          {filtered.length === 0 && <div className={css.noMatch}>无匹配报告——调整搜索词或筛选条件。</div>}
        </div>
      )}
    </div>
  )
}
