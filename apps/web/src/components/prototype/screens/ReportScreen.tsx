// ReportScreen：报告视图。Demo 任务显示完整 PTES 报告；非 demo 显示空状态。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

export function ReportScreen({ db, currentTask }: { db: DBShape; currentTask: Task | null }) {
  const demo = currentTask?.demo === true || db.meta.curTaskId === 't3'
  return (
    <div className="proto-screen on" style={{ overflow: 'hidden' }}>
      {demo ? <ReportDemo task={currentTask} /> : (
        <div className="proto-page">
          <div className="proto-empty">
            <div className="e-icon">📄</div>
            <div className="e-t">该任务报告生成中</div>
            <div className="e-d">执行完成后可一键生成 PTES 结构报告并导出。</div>
          </div>
        </div>
      )}
    </div>
  )
}

function ReportDemo({ task }: { task: Task | null }) {
  const [tab, setTab] = useState(1)
  const items = [
    { id: 1, t: '1. 执行摘要', sub: false },
    { id: 2, t: '2. 授权范围与方法论', sub: false },
    { id: 3, t: '3. 攻击路径重建', sub: false },
    { id: 31, t: '3.1 初始访问', sub: true },
    { id: 32, t: '3.2 权限维持', sub: true },
    { id: 4, t: '4. 风险明细', sub: false },
    { id: 5, t: '5. 修复建议汇总', sub: false },
    { id: 6, t: '6. 附录 · 工具清单', sub: false },
  ]
  const target = task?.target ?? 'demo.ine.local'
  return (
    <div className="proto-report-layout">
      <div className="proto-toc">
        <div style={{ padding: '20px 20px 6px', fontSize: 15, fontWeight: 700 }}>渗透测试报告 · {target}</div>
        <div style={{ padding: '0 20px 14px', fontSize: 11, color: 'var(--text-3)' }} className="mono">{target} · 2026-09-07</div>
        {items.map((it) => (
          <span
            key={it.id}
            className={'proto-toc-item' + (it.sub ? ' sub' : '') + (tab === it.id ? ' on' : '')}
            onClick={() => setTab(it.id)}
          >
            {it.t}
          </span>
        ))}
      </div>
      <div className="proto-doc-view">
        <div style={{ maxWidth: 780, margin: '0 auto 16px', display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="proto-btn ghost">导出 Markdown</button>
          <button className="proto-btn ghost">导出 JSON</button>
          <button className="proto-btn primary">导出 PDF</button>
        </div>
        <div className="proto-doc">
          <h1>{target} Web 渗透测试报告</h1>
          <div className="doc-sub mono">任务编号 ENG-20260907-0142 · 执行时间 2026-09-07 21:02 – 23:40 · 智能体 破阵 v5.0 · 复核人 ______</div>
          <h2>1. 执行摘要</h2>
          <p>本次授权测试针对 {target} 及其关联内网段（10.0.8.0/24）开展。智能体共执行 <b>42</b> 次工具调用，其中 <b>3</b> 次高危动作经人工审批后执行，发现 <b>16</b> 项风险，其中<strong style={{ color: "var(--crit)" }}>严重 1 项、高危 3 项</strong>。</p>
          <p>核心结论：目标 Tomcat 服务（:8080）存在可利用的远程代码执行漏洞（CVE-2024-50379），结合 Manager 弱口令，攻击者可获得目标主机完全控制权，并可能借数据库凭据实现内网横向移动。<b>建议 72 小时内完成紧急修复。</b></p>
          <div className="risk-inline"><b>严重 · CVE-2024-50379 — Tomcat PUT 远程代码执行</b><div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 4 }}>CVSS 9.8 · {target}:8080 · 已验证回显 · 探测文件已自动清理</div></div>
          <div className="risk-inline" style={{ borderLeftColor: 'var(--high)' }}><b>高危 · /backup 目录敏感信息泄露</b><div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 4 }}>CVSS 7.5 · 含数据库明文密码与 .git 历史，可直接导致二次入侵</div></div>
          <h2>2. 授权范围与方法论</h2>
          <ul>
            <li>授权目标：{target}、10.0.8.0/24（排除 10.0.8.1 网关）</li>
            <li>禁用动作：DoS 类、社工钓鱼、数据破坏</li>
            <li>方法论：Kill Chain 七阶段 · PTES 流程 · 每步证据留存</li>
          </ul>
          <h2>3. 攻击路径重建</h2>
          <h3>3.1 初始访问</h3>
          <p>端口扫描（nmap）发现 :8080 Tomcat Manager → 弱口令字典爆破（hydra，T2 审批）获得 tomcat:s3cret → 利用 PUT 上传 JSP 探测文件（T2 审批）确认 RCE。</p>
          <h3>3.2 权限维持评估</h3>
          <p>仅做理论评估未实际部署持久化后门（属 T3 禁区动作，已在方案中说明）。</p>
          <h2>4. 风险明细</h2>
          <p>共 16 项：严重 1 · 高危 3 · 中危 5 · 低危 7，逐项含证据链、CVSS 与修复建议（见风险页关联视图）。</p>
        </div>
      </div>
    </div>
  )
}