/**
 * ReportPreview：报告实时投影预览（F3 details）。数据 = projection.report
 * （parseReport）。状态行（撰写中/就绪）+ 摘要 + 发现列表（severity 徽章）；
 * 「完整报告」按钮弹 Modal 全屏排版（章节 + 全部发现 + 负面结论占位提示）；
 * 「导出报告」下拉（Markdown / JSON）走 downloadReport 直接取文件流落盘。
 */

import { useState } from 'react'
import type { ReportProjection } from '../../runtime/projections.ts'
import { formatDateTime } from '../../runtime/format.ts'
import { downloadReport } from '../../connection/api.ts'
import { Button } from '../primitives/Button.tsx'
import { Modal } from '../primitives/Modal.tsx'
import { SEVERITY_LABEL } from './severity.ts'
import sevCss from './severity.module.css'
import css from './ReportPreview.module.css'

export interface ReportPreviewProps {
  report: ReportProjection | null
  /** 当前任务书 id（导出产物按 engagement 落盘；缺省隐藏导出入口）。 */
  engagementId?: string
}

type ReportFormat = 'md' | 'json'

/** 触发一次报告生成落盘后按格式下载（下载失败 alert 行内提示）。 */
async function exportReport(engagementId: string, format: ReportFormat): Promise<void> {
  try {
    await downloadReport(engagementId, format)
  } catch (cause) {
    window.alert(`导出报告失败：${cause instanceof Error ? cause.message : String(cause)}`)
  }
}

export function ReportPreview({ report, engagementId }: ReportPreviewProps) {
  const [open, setOpen] = useState(false)
  if (report === null) {
    return (
      <div className={css.empty}>
        报告投影尚未就绪（R5 报告引擎）——撰写完成后会实时投影到这里，可预览并全屏查看。
      </div>
    )
  }
  const findings = report.findings ?? []
  const sections = report.sections ?? []
  return (
    <div className={css.root}>
      <div className={css.statusRow}>
        <span className={css.status} data-status={report.status}>
          {report.status === 'ready' ? '报告已就绪' : '撰写中…'}
        </span>
        {report.generatedAt !== undefined && (
          <span className={css.gen}>生成于 {formatDateTime(report.generatedAt)}</span>
        )}
      </div>
      {report.summary !== undefined && report.summary !== '' && (
        <p className={css.summary}>{report.summary}</p>
      )}
      <div className={css.actions}>
        <span className={css.meta}>章节 {sections.length} · 发现 {findings.length}</span>
        <span className={css.actionButtons}>
          {engagementId !== undefined && (
            <>
              <Button size="sm" onClick={() => void exportReport(engagementId, 'md')}>导出 Markdown</Button>
              <Button size="sm" onClick={() => void exportReport(engagementId, 'json')}>导出 JSON</Button>
              <Button size="sm" onClick={() => void exportReport(engagementId, 'pdf')}>导出 PDF</Button>
            </>
          )}
          <Button size="sm" onClick={() => setOpen(true)} disabled={sections.length === 0 && findings.length === 0}>
            完整报告
          </Button>
        </span>
      </div>
      {findings.length > 0 && (
        <ol className={css.findings}>
          {findings.slice(0, 6).map((finding) => (
            <li key={finding.id} className={css.finding}>
              <span className={sevCss.sev} data-sev={finding.severity}>{SEVERITY_LABEL[finding.severity]}</span>
              <span className={css.findingTitle}>{finding.title}</span>
            </li>
          ))}
        </ol>
      )}
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="完整报告预览"
        footer={<Button onClick={() => setOpen(false)}>关闭</Button>}
      >
        <div className={css.reportBody}>
          {report.summary !== undefined && report.summary !== '' && (
            <p className={css.reportSummary}>{report.summary}</p>
          )}
          {sections.map((section) => (
            <section key={section.id} className={css.reportSection}>
              <h3 className={css.reportH}>{section.title}</h3>
              {section.body !== undefined && <div className={css.reportP}>{section.body}</div>}
            </section>
          ))}
          {findings.length > 0 && (
            <section className={css.reportSection}>
              <h3 className={css.reportH}>发现明细</h3>
              <ol className={css.reportFindings}>
                {findings.map((finding) => (
                  <li key={finding.id} className={css.reportFinding}>
                    <div className={css.reportFindingHead}>
                      <span className={sevCss.sev} data-sev={finding.severity}>{SEVERITY_LABEL[finding.severity]}</span>
                      <span className={css.findingTitle}>{finding.title}</span>
                    </div>
                    {finding.summary !== undefined && <div className={css.reportP}>{finding.summary}</div>}
                    {finding.evidence !== undefined && finding.evidence.length > 0 && (
                      <ul className={css.evidenceList}>
                        {finding.evidence.map((evidence) => <li key={evidence}>{evidence}</li>)}
                      </ul>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
      </Modal>
    </div>
  )
}
