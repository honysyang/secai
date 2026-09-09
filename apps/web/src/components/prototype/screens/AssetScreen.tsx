// AssetScreen：资产视图。Demo 任务 = 完整表格；非 Demo = 空状态。
import type { DBShape, Task } from '../db.ts'

export function AssetScreen({ db, currentTask }: { db: DBShape; currentTask: Task | null }) {
  const demo = currentTask?.demo === true || db.meta.curTaskId === 't3'
  return (
    <div className="proto-screen on">
      <div className="proto-page">
        <div className="proto-page-head">
          <span className="proto-page-title">资产</span>
          <span className="proto-page-sub">当前范围：{currentTask?.name ?? '—'} · {currentTask?.target ?? '—'}{currentTask?.atts && currentTask.atts.length > 0 ? ` · 授权材料 ${currentTask.atts.length} 份` : ''}</span>
        </div>
        {demo ? <DemoBody /> : <EmptyState icon="🛰️" title="该任务尚在侦察 / 计划中" desc="资产与风险数据将随智能体执行自动生成。" />}
      </div>
    </div>
  )
}

function DemoBody() {
  return (
    <>
      <div className="proto-stat-grid">
        <Stat label="主机" value="14" />
        <Stat label="域名" value="7" />
        <Stat label="开放服务" value="36" />
        <Stat label="有效凭据" value="2" />
        <Stat label="新增（24h）" value="+3" color="var(--brand-600)" />
      </div>
      <div className="proto-two-col">
        <div>
          <div className="proto-chips">
            <button className="proto-chip on">全部</button>
            <button className="proto-chip">Web</button>
            <button className="proto-chip">数据库</button>
            <button className="proto-chip">域控</button>
            <button className="proto-chip">网络设备</button>
            <button className="proto-chip">凭据</button>
          </div>
          <div className="proto-card">
            <table className="proto-tbl">
              <thead><tr><th>目标</th><th>类型</th><th>OS / 指纹</th><th>服务 · 端口</th><th>首次发现</th></tr></thead>
              <tbody>
                {[
                  ['demo.ine.local', 'Web', 'Apache 2.4.57', 'http/80 · ssl/443', '09-07 21:03'],
                  ['10.0.8.23', 'Web', 'Tomcat 9.0.83', 'http/8080', '09-07 21:05'],
                  ['10.0.8.5', '数据库', 'MySQL 8.0.36', 'mysql/3306', '09-07 21:09'],
                  ['10.0.8.10', '域控', 'Windows Server 2022', 'ldap/389 · smb/445', '09-07 21:12'],
                  ['vpn.corp.example', '网络设备', 'FortiOS 7.4', 'ssl/10443', '09-06 18:40'],
                  ['mail.corp.example', 'Web', 'Exchange 2019 CU14', 'http/80 · mapi/443', '09-05 10:22'],
                ].map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{r[0]}</td>
                    <td>{r[1]}</td>
                    <td>{r[2]}</td>
                    <td className="mono">{r[3]}</td>
                    <td className="mono" style={{ color: 'var(--text-3)' }}>{r[4]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="proto-card proto-detail-card">
          <div className="proto-detail-head">
            <span className="proto-detail-title">demo.ine.local</span>
            <span className="proto-badge soft-ok">活跃</span>
          </div>
          <div className="proto-detail-body">
            <div className="proto-kv"><span className="k">类型</span><span className="v">Web · Apache 2.4.57</span></div>
            <div className="proto-kv"><span className="k">端口</span><span className="v">22, 80, 443, 8080</span></div>
            <div className="proto-kv"><span className="k">技术栈</span><span className="v">PHP 8.2, jQuery 3.6</span></div>
            <div className="proto-kv"><span className="k">发现方式</span><span className="v">nmap -sV ( 任务 21:03)</span></div>
            <div>
              <div className="proto-io-label" style={{ marginBottom: 6 }}>关联风险</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span><span className="proto-badge solid-crit">严重</span> <span style={{ fontSize: 12 }}>Tomcat PUT RCE (CVE-2024-50379)</span></span>
                <span><span className="proto-badge soft-high">高危</span> <span style={{ fontSize: 12 }}>/backup 目录敏感信息泄露</span></span>
              </div>
            </div>
            <div>
              <div className="proto-io-label" style={{ marginBottom: 6 }}>最新指纹证据</div>
              <div className="proto-ev-block">HTTP/1.1 200 OK
Server: Apache/2.4.57 (Unix)
X-Powered-By: PHP/8.2.18
Set-Cookie: PHPSESSID=…; HttpOnly</div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="proto-stat-card">
      <div className="proto-stat-label">{label}</div>
      <div className="proto-stat-num" style={color ? { color } : undefined}>{value}</div>
    </div>
  )
}

function EmptyState({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div className="proto-empty">
      <div className="e-icon">{icon}</div>
      <div className="e-t">{title}</div>
      <div className="e-d">{desc}</div>
    </div>
  )
}