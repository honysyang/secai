/**
 * ReportPanel：报告管理视图。数据 = host/reports 帧（AppSnapshot.reports）。
 * 卡片式呈现报告元信息：标题 + 状态 + 发现统计 + 产物下载入口。
 */

import type { ReportEntry } from '../../connection/api.ts'
import { downloadReport } from '../../connection/api.ts'
import { formatDateTime } from '../../runtime/format.ts'
import { Button } from '../primitives/Button.tsx'
import css from './ReportPanel.module.css'

export interface ReportPanelProps {
  reports: readonly ReportEntry[]
}

/** 触发报告导出下载（md/json/pdf 三格式）。 */
async function exportReport(engagementId: string, format: 'md' | 'json' | 'pdf'): Promise<void> {
  try {
    await downloadReport(engagementId, format)
  } catch (cause) {
    window.alert(`导出报告失败：${cause instanceof Error ? cause.message : String(cause)}`)
  }
}

function ReportCard({ report }: { report: ReportEntry }) {
  return (
    <div className={css.card}>
      <div className={css.cardHead}>
        <span className={css.cardTitle}>{report.title}</span>
        <span className={css.status} data-status={report.status}>
          {report.status === 'ready' ? '已就绪' : '撰写中'}
        </span>
      </div>
      <div className={css.cardMeta}>
        {report.generatedAt !== undefined && (
          <span>生成于 {formatDateTime(report.generatedAt)}</span>
        )}
        <span>发现 {report.findingsCount} 项</span>
        {report.severityCounts !== undefined && (
          <span className={css.sevCounts}>
            {Object.entries(report.severityCounts).map(([sev, count]) => (
              <span key={sev} className={css.sevCount} data-sev={sev}>
                {sev} {count}
              </span>
            ))}
          </span>
        )}
      </div>
      <div className={css.cardActions}>
        <Button size="sm" variant="outline" onClick={() => void exportReport(report.engagementId, 'md')}>
          导出 Markdown
        </Button>
        <Button size="sm" variant="outline" onClick={() => void exportReport(report.engagementId, 'pdf')}>
          导出 PDF
        </Button>
        <Button size="sm" variant="outline" onClick={() => void exportReport(report.engagementId, 'json')}>
          导出 JSON
        </Button>
      </div>
    </div>
  )
}

export function ReportPanel({ reports }: ReportPanelProps) {
  if (reports.length === 0) {
    return (
      <div className={css.empty}>
        暂无报告——agent 完成渗透测试后会在此生成可交付的 Markdown / PDF / JSON 报告。
      </div>
    )
  }
  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>报告中心</span>
        <span className={css.count}>{reports.length} 份</span>
      </div>
      <div className={css.grid}>
        {reports.map((report) => (
          <ReportCard key={report.id} report={report} />
        ))}
      </div>
    </div>
  )
}
