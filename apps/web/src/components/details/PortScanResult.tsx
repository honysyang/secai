/**
 * PortScanResult：结构化端口表格（F3 details）。数据 = projection.
 * attack_surface（parseAttackSurface 的 ports）。端口升序表格；服务列
 * 归一化标签（http/ssh/…），版本可空，状态 open 绿字。
 */

import type { AttackSurfaceProjection } from '../../runtime/projections.ts'
import css from './PortScanResult.module.css'

export interface PortScanResultProps {
  surface: AttackSurfaceProjection | null
}

export function PortScanResult({ surface }: PortScanResultProps) {
  if (surface === null || surface.ports.length === 0) {
    return (
      <div className={css.empty}>
        攻击面投影尚未就绪（projection.attack_surface，R4 结构化回流）——扫描到的端口与服务会以表格呈现。
      </div>
    )
  }
  const ports = [...surface.ports].sort((a, b) => a.port - b.port)
  return (
    <div className={css.root}>
      {surface.targets !== undefined && surface.targets.length > 0 && (
        <div className={css.targets}>
          {surface.targets.map((target) => (
            <span key={target} className={css.target}>{target}</span>
          ))}
        </div>
      )}
      <div className={css.wrap}>
        <table className={css.table}>
          <thead>
            <tr>
              <th className={css.th}>端口</th>
              <th className={css.th}>协议</th>
              <th className={css.th}>服务</th>
              <th className={css.th}>版本</th>
              <th className={css.th}>状态</th>
            </tr>
          </thead>
          <tbody>
            {ports.map((row) => (
              <tr key={row.port} className={css.tr}>
                <td className={css.port}>{row.port}</td>
                <td className={css.td}>{row.protocol ?? 'tcp'}</td>
                <td className={css.service}>{row.service ?? '—'}</td>
                <td className={css.version}>{row.version ?? ''}</td>
                <td className={css.td}>
                  <span className={css.state} data-open={row.state === 'open' || undefined}>{row.state ?? 'open'}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
