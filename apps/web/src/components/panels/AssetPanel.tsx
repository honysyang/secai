/**
 * AssetPanel：资产管理视图。数据 = host/assets 帧（AppSnapshot.assets）。
 * 表格呈现授权目标的结构化信息：目标、类型、OS、服务、端口、首次/末次发现时间。
 */

import type { AssetEntry } from '../../connection/api.ts'
import { formatDateTime } from '../../runtime/format.ts'
import css from './AssetPanel.module.css'

export interface AssetPanelProps {
  assets: readonly AssetEntry[]
}

export function AssetPanel({ assets }: AssetPanelProps) {
  if (assets.length === 0) {
    return (
      <div className={css.empty}>
        暂无资产——agent 完成侦察后会在此登记授权目标的结构化信息（类型 / OS / 服务 / 端口）。
      </div>
    )
  }
  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.title}>资产清单</span>
        <span className={css.count}>{assets.length} 项</span>
      </div>
      <div className={css.wrap}>
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
            {assets.map((asset) => (
              <tr key={asset.id} className={css.tr}>
                <td className={css.target}>{asset.target}</td>
                <td className={css.td}>
                  <span className={css.kind}>{asset.kind}</span>
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
          </tbody>
        </table>
      </div>
    </div>
  )
}
