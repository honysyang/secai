/**
 * ArsenalPanel：武器库视图（按 Kill Chain 七阶段展示当前系统支持的安全工具）。
 * 数据 = /api/tools（AppSnapshot.tools，含 killChain + installed）。
 * 结构 = 头部（标题 + 搜索框 + 安装统计）+ 阶段分组（七阶段列/卡片）
 * + 工具表格（名称/命令/阶段/安装状态/描述）。
 */

import { useMemo, useState } from 'react'
import type { KillChainPhase, ToolEntry } from '../../connection/api.ts'
import { EmptyState } from '../primitives/EmptyState.tsx'
import css from './ArsenalPanel.module.css'

export interface ArsenalPanelProps {
  tools: readonly ToolEntry[]
}

/** Kill Chain 七阶段（顺序展示）。 */
const KILL_CHAIN_PHASES: ReadonlyArray<{ key: KillChainPhase; label: string }> = [
  { key: 'recon', label: '侦察 (Recon)' },
  { key: 'weaponization', label: '武器化 (Weaponization)' },
  { key: 'delivery', label: '投递 (Delivery)' },
  { key: 'exploitation', label: '利用 (Exploitation)' },
  { key: 'installation', label: '安装 (Installation)' },
  { key: 'c2', label: 'C2 控制' },
  { key: 'actions', label: '行动 (Actions)' },
]

export function ArsenalPanel({ tools }: ArsenalPanelProps) {
  const [query, setQuery] = useState('')
  const [phase, setPhase] = useState<KillChainPhase | 'all'>('all')

  const installed = useMemo(() => tools.filter((t) => t.installed).length, [tools])

  const byPhase = useMemo(() => {
    const map = new Map<KillChainPhase, ToolEntry[]>()
    for (const phase of KILL_CHAIN_PHASES) map.set(phase.key, [])
    for (const tool of tools) {
      const list = map.get(tool.killChain)
      if (list !== undefined) list.push(tool)
    }
    return map
  }, [tools])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return tools.filter((tool) => {
      if (phase !== 'all' && tool.killChain !== phase) return false
      if (q === '') return true
      return tool.name.toLowerCase().includes(q) || tool.shortDescription.toLowerCase().includes(q)
    })
  }, [tools, query, phase])

  return (
    <div className={css.root}>
      <div className={css.header}>
        <h1 className={css.title}>武器库</h1>
        <span className={css.subtitle}>按 Kill Chain 阶段展示当前系统支持的安全工具</span>
        <div className={css.spacer} />
        <input
          type="search"
          className={css.search}
          placeholder="搜索工具…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className={css.stats}>
        <span className={css.statCard}>
          <span className={css.statValue}>{tools.length}</span>
          <span className={css.statLabel}>总工具</span>
        </span>
        <span className={css.statCard}>
          <span className={css.statValue}>{installed}</span>
          <span className={css.statLabel}>已安装</span>
        </span>
        <span className={css.statCard}>
          <span className={css.statValue}>{tools.length - installed}</span>
          <span className={css.statLabel}>未安装</span>
        </span>
      </div>

      <div className={css.phaseChips}>
        <button
          type="button"
          className={`${css.chip} ${phase === 'all' ? css.chipActive : ''}`}
          onClick={() => setPhase('all')}
        >
          全部 ({tools.length})
        </button>
        {KILL_CHAIN_PHASES.map((p) => {
          const count = byPhase.get(p.key)?.length ?? 0
          return (
            <button
              key={p.key}
              type="button"
              className={`${css.chip} ${phase === p.key ? css.chipActive : ''}`}
              onClick={() => setPhase(p.key)}
            >
              {p.label} ({count})
            </button>
          )
        })}
      </div>

      <div className={css.list}>
        {filtered.length === 0 ? (
          <EmptyState
            title="无匹配工具"
            description="调整搜索或阶段筛选条件。"
          />
        ) : (
          filtered.map((tool) => (
            <div key={tool.name} className={css.toolRow} data-installed={tool.installed || undefined}>
              <span className={css.toolName}>{tool.name}</span>
              <code className={css.toolCommand}>{tool.command}</code>
              <span className={css.toolPhase}>{KILL_CHAIN_PHASES.find((p) => p.key === tool.killChain)?.label ?? tool.killChain}</span>
              <span className={css.toolStatus} data-installed={tool.installed || undefined}>
                {tool.installed ? '✓ 已安装' : '✗ 未安装'}
              </span>
              <span className={css.toolDesc}>{tool.shortDescription}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
