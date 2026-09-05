/**
 * LearningPanel：L6 成长度量（F3 details）。数据 = projection.growth
 * （parseGrowth）。三度量卡（假设命中率 / token 效率 / 剧本复用）+ 横向
 * 比例条；有 series 时给出近轮命中率迷你趋势行。
 */

import type { GrowthMetrics } from '../../runtime/projections.ts'
import css from './LearningPanel.module.css'

export interface LearningPanelProps {
  growth: GrowthMetrics | null
}

function pct(value: number): string {
  return `${Math.max(0, Math.min(100, Math.round(value * 100)))}%`
}

function whole(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value * 100)))
}

const METRICS: ReadonlyArray<{ key: 'hitRate' | 'tokenEfficiency' | 'playbookReuse'; label: string }> = [
  { key: 'hitRate', label: '假设命中率' },
  { key: 'tokenEfficiency', label: 'Token 效率' },
  { key: 'playbookReuse', label: '剧本复用' },
]

export function LearningPanel({ growth }: LearningPanelProps) {
  if (growth === null) {
    return (
      <div className={css.empty}>
        成长度量尚未沉淀（projection.growth，R6 L6 学习层）——多轮任务后命中率 / token 效率 / 剧本复用趋势会在此累积。
      </div>
    )
  }
  return (
    <div className={css.root}>
      <div className={css.cards}>
        {METRICS.map((metric) => {
          const value = growth[metric.key]
          return (
            <div key={metric.key} className={css.card}>
              <div className={css.value}>
                {whole(value)}
                <span className={css.unit}>%</span>
              </div>
              <div className={css.label}>{metric.label}</div>
              <div className={css.track}>
                <span className={css.fill} data-key={metric.key} style={{ width: pct(value) }} />
              </div>
            </div>
          )
        })}
      </div>
      {growth.series !== undefined && (
        <div className={css.trend}>
          <div className={css.trendHead}>命中率 · 近 {growth.series.length} 轮</div>
          {growth.series.map((point) => (
            <div key={point.label} className={css.tRow}>
              <span className={css.tLabel}>{point.label}</span>
              <span className={css.tBar}>
                <span className={`${css.tFill} ${css.tHit}`} style={{ width: pct(point.hitRate) }} />
              </span>
              <span className={css.tVal}>{whole(point.hitRate)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
