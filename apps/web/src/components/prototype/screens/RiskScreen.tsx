// RiskScreen：风险视图。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

export function RiskScreen({ db, currentTask }: { db: DBShape; currentTask: Task | null }) {
  const demo = currentTask?.demo === true || db.meta.curTaskId === 't3'
  return (
    <div className="proto-screen on">
      <div className="proto-page">
        <div className="proto-page-head">
          <span className="proto-page-title">风险</span>
          <span className="proto-page-sub">当前范围：{currentTask?.name ?? '—'} · {currentTask?.target ?? '—'}</span>
        </div>
        {demo ? <RiskDemo /> : (
          <div className="proto-empty">
            <div className="e-icon">🛡️</div>
            <div className="e-t">该任务暂无确认风险</div>
            <div className="e-d">风险将在智能体执行利用与验证后自动归集至此。</div>
          </div>
        )}
      </div>
    </div>
  )
}

function RiskDemo() {
  const [openIdx, setOpenIdx] = useState<number>(0)
  const risks = [
    { tone: 'solid-crit', title: 'Tomcat PUT 方法远程代码执行（CVE-2024-50379）', meta: 'demo.ine.local:8080 · CVSS 9.8', evidence: '[1] PUT /upload/probe.jsp → 201 Created (nmap http-put, 21:07:44)\n[2] GET /upload/probe.jsp → 200 · 执行 echo "SECAI-PROBE" 输出回显 (curl, 21:07:46)\n[3] 探测文件 5 秒后自动删除 → 404 (复验通过)', fix: '升级 Tomcat 至 9.0.86+；禁用 HTTP PUT/DELETE 方法（web.xml 中 readonly=false 移除）；在 WAF 层拦截 /upload 路径异常写入。' },
    { tone: 'soft-high', title: '/backup 目录暴露全站源码与数据库备份', meta: 'demo.ine.local · CVSS 7.5', evidence: 'GET /backup/site-2026-08-30.tar.gz → 200 (14.2MB)\n内容含: config.php (数据库明文密码), .git 目录, uploads/' },
    { tone: 'soft-high', title: 'Tomcat Manager 默认凭据（tomcat:s3cret）', meta: '10.0.8.23:8080 · CVSS 8.1', evidence: 'hydra -L users.txt -P pass.txt 10.0.8.23 http-get /manager/html\n[8080][http-get] host: 10.0.8.23  login: tomcat  password: s3cret' },
    { tone: 'soft-med', title: 'PHPSESSID Cookie 缺少 Secure 属性', meta: 'demo.ine.local · CVSS 5.3', evidence: 'Set-Cookie: PHPSESSID=…; HttpOnly  (缺少 Secure 标志)' },
  ]
  return (
    <>
      <div className="proto-stat-grid">
        <Stat label="严重 Critical" value="1" color="var(--crit)" band="var(--crit)" active />
        <Stat label="高危 High" value="3" color="var(--high)" band="var(--high)" />
        <Stat label="中危 Medium" value="5" color="var(--med)" band="var(--med)" />
        <Stat label="低危 Low" value="7" color="var(--low)" band="var(--low)" />
        <Stat label="信息 Info" value="12" color="var(--text-2)" band="var(--text-3)" />
      </div>
      <div className="proto-chips">
        <button className="proto-chip on">全部状态</button>
        <button className="proto-chip">待验证</button>
        <button className="proto-chip">已确认</button>
        <button className="proto-chip">已修复</button>
        <button className="proto-chip">误报</button>
      </div>
      {risks.map((r, i) => (
        <div key={i} className={'proto-risk-row' + (openIdx === i ? ' open' : '') + (r.tone === 'solid-crit' ? ' crit-row' : '')}>
          <div className="proto-risk-head" onClick={() => setOpenIdx(openIdx === i ? -1 : i)}>
            <span className={'proto-badge ' + r.tone}>
              {r.tone === 'solid-crit' ? '严重' : r.tone === 'soft-high' ? '高危' : r.tone === 'soft-med' ? '中危' : '低危'}
            </span>
            <span className="proto-risk-title">{r.title}</span>
            <span className="proto-risk-meta mono">{r.meta}</span>
            <select className="proto-sb-sel" defaultValue="已确认">
              <option>已确认</option><option>待验证</option><option>误报</option><option>已修复</option>
            </select>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--text-3)' }}>
              <path d="m6 9 6 6 6-6" />
            </svg>
          </div>
          {openIdx === i && (
            <div className="proto-risk-ev">
              <div className="proto-io-label" style={{ marginBottom: 6 }}>证据链</div>
              <div className="proto-ev-block">{r.evidence}</div>
              {r.fix && (
                <>
                  <div className="proto-io-label" style={{ margin: '10px 0 6px' }}>修复建议</div>
                  <div style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.7 }}>{r.fix}</div>
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </>
  )
}

function Stat({ label, value, color, band, active }: { label: string; value: string; color?: string; band?: string; active?: boolean }) {
  return (
    <div className={'proto-stat-card clickable' + (active ? ' on' : '')} style={band ? { '--band': band } as React.CSSProperties : undefined}>
      <div className="proto-stat-label">{label}</div>
      <div className="proto-stat-num" style={color ? { color } : undefined}>{value}</div>
    </div>
  )
}