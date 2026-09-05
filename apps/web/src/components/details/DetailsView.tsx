/**
 * DetailsView：右侧详情栏组合（F3 编排）。从当前 SessionSnapshot 的
 * projections 解析出各视图数据并分发给五个详情组件；标题栏带目标标识。
 * 内容区滚动：工具输出 → 证据链 → 端口与服务 → 报告预览 → 学习与成长。
 */

import type { ReactNode } from 'react'
import type { SessionSnapshot } from '../../runtime/session.ts'
import {
  parseAttackSurface,
  parseDeadEnds,
  parseEvidence,
  parseGrowth,
  parseReport,
} from '../../runtime/projections.ts'
import { EvidenceChain } from './EvidenceChain.tsx'
import { LearningPanel } from './LearningPanel.tsx'
import { PortScanResult } from './PortScanResult.tsx'
import { ReportPreview } from './ReportPreview.tsx'
import { ToolOutputPanel } from './ToolOutputPanel.tsx'
import css from './DetailsView.module.css'

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className={css.section}>
      <div className={css.secHead}>
        <span className={css.secTitle}>{title}</span>
        {hint !== undefined && <span className={css.secHint}>{hint}</span>}
      </div>
      <div className={css.secBody}>{children}</div>
    </section>
  )
}

export interface DetailsViewProps {
  session: SessionSnapshot | null
}

export function DetailsView({ session }: DetailsViewProps) {
  const surface = session !== null ? parseAttackSurface(session.projections.attack_surface) : null
  const chains = session !== null ? parseEvidence(session.projections.evidence) : null
  const report = session !== null ? parseReport(session.projections.report) : null
  const growth = session !== null ? parseGrowth(session.projections.growth) : null
  const deadEndCount = session !== null ? (parseDeadEnds(session.projections.dead_ends)?.length ?? 0) : 0

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.headTitle}>详情</span>
        <span className={css.headTarget} title={session?.header?.target}>
          {session?.header?.target ?? '未选择目标'}
        </span>
      </div>
      {session === null ? (
        <div className={css.empty}>从左侧选择一个目标会话——工具输出、证据链、端口表、报告与成长度量会在此汇集。</div>
      ) : (
        <div className={css.body}>
          <Section title="工具输出">
            <ToolOutputPanel events={session.events} />
          </Section>
          <Section title="证据链" hint={chains !== null ? `${chains.length} 条` : 'projection.evidence'}>
            <EvidenceChain chains={chains} />
          </Section>
          <Section title="端口与服务" hint={surface !== null ? `${surface.ports.length} 个端口` : 'projection.attack_surface'}>
            <PortScanResult surface={surface} />
          </Section>
          <Section title="报告预览" hint={report !== null ? `发现 ${report.findings?.length ?? 0}` : 'projection.report'}>
            <ReportPreview report={report} />
          </Section>
          <Section title="学习与成长" hint={deadEndCount > 0 ? `死路 ${deadEndCount}` : 'projection.growth'}>
            <LearningPanel growth={growth} />
          </Section>
        </div>
      )}
    </div>
  )
}
