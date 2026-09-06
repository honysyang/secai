/**
 * AssetPanel：资产管理视图（r6 用户视角重设计）。
 * 数据 = host/assets 帧 + bootstrap 拉取（AppSnapshot.assets）。
 * 结构 = 头部（标题 + 搜索框）+ 类型筛选 chips + 统计条（总资产/端口/服务）
 * + 资产表格（目标/类型/OS/服务/端口/首末次发现）。
 */

import { useMemo, useState } from 'react'
import type { AssetEntry } from '../../connection/api.ts'
import { formatDateTime } from '../../runtime/format.ts'
import css from './AssetPanel.module.css'

export interface AssetPanelProps {
  assets: readonly AssetEntry[]
}

/** 类型中文标签。 */
const KIND_LABEL: Record<string, string> = {
  host: '主机',
  'web-app': 'Web 应用',
  'network-segment': '网段',
}

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

export function AssetPanel({ assets }: AssetPanelProps) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<string>('all')

  const kinds = useMemo(() => {
    const counts = new Map<string, number>()
    for (const asset of assets) {
      counts.set(asset.kind, (counts.get(asset.kind) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [assets])

  const totalPorts = useMemo(() => assets.reduce((sum, asset) => sum + asset.ports.length, 0), [assets])
  const totalServices = useMemo(
    () => assets.reduce((sum, asset) => sum + asset.services.length, 0),
    [assets],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return assets.filter((asset) => {
      if (kind !== 'all' && asset.kind !== kind) return false
      if (q === '') return true
      const haystack = [
        asset.target,
        asset.os ?? '',
        asset.kind,
        ...asset.services,
        ...asset.ports.map(String),
      ]
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
  }, [assets, kind, query])

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>资产清单</span>
        <span className={css.count}>{assets.length} 项 · {totalPorts} 端口 · {totalServices} 服务</span>
        <span className={css.spacer} />
        <input
          className={css.search}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索目标 / 服务 / 端口…"
          aria-label="搜索资产"
        />
      </div>

      <div className={css.filters}>
        <button
          type="button"
          className={css.chip}
          data-active={kind === 'all' || undefined}
          onClick={() => setKind('all')}
        >
          全部 {assets.length}
        </button>
        {kinds.map(([key, count]) => (
          <button
            key={key}
            type="button"
            className={css.chip}
            data-active={kind === key || undefined}
            onClick={() => setKind(key)}
          >
            {kindLabel(key)} {count}
          </button>
        ))}
      </div>

      <div className={css.wrap}>
        {assets.length === 0 ? (
          <div className={css.empty}>
            暂无资产——agent 完成侦察后会在此登记授权目标的结构化信息（类型 / OS / 服务 / 端口）。
          </div>
        ) : (
        <table className={css.table}>
          <thead>
            <tr>
              <th className={css.th}>目标</th>
              <th className={css.th}>类型</th>
              <th className={css.th}>OS</th>
              <th className={css.th}>服务</th>
              <th className={css.th}>端口</th>
              <th className={css.th}>首次发现</th>
              <th className={css.th}>最近更新</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((asset) => (
              <tr key={asset.id} className={css.tr}>
                <td className={css.target}>{asset.target}</td>
                <td className={css.td}>
                  <span className={css.kind}>{kindLabel(asset.kind)}</span>
                </td>
                <td className={css.td}>{asset.os ?? '—'}</td>
                <td className={css.td}>
                  <div className={css.tags}>
                    {asset.services.map((svc) => (
                      <span key={svc} className={css.tag}>{svc}</span>
                    ))}
                  </div>
                </td>
                <td className={css.td}>
                  <div className={css.tags}>
                    {asset.ports.map((port) => (
                      <span key={port} className={css.tag}>{port}</span>
                    ))}
                  </div>
                </td>
                <td className={css.time}>{formatDateTime(asset.firstSeenAt)}</td>
                <td className={css.time}>{formatDateTime(asset.lastSeenAt)}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td className={css.td} colSpan={7}>无匹配资产——调整搜索词或筛选条件。</td>
              </tr>
            )}
          </tbody>
        </table>
        )}
      </div>
    </div>
  )
}
