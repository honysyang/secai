// ArsenalScreen：武器库视图（42 工具 × Kill Chain 7 阶段）。
import type { DBShape, Task } from '../db.ts'

export function ArsenalScreen({ currentTask: _currentTask }: { db: DBShape; currentTask: Task | null }) {
  return (
    <div className="proto-screen on">
      <div className="proto-page">
        <div className="proto-page-head">
          <span className="proto-page-title">武器库</span>
          <span className="proto-page-sub">42 个工具 · 按 Kill Chain 阶段编排</span>
        </div>
        <div className="proto-stat-grid">
          <div className="proto-stat-card"><div className="proto-stat-label">已安装</div><div className="proto-stat-num">36</div></div>
          <div className="proto-stat-card"><div className="proto-stat-label">可安装</div><div className="proto-stat-num">6</div></div>
          <div className="proto-stat-card"><div className="proto-stat-label">需授权 (T3+)</div><div className="proto-stat-num" style={{ color: 'var(--med)' }}>4</div></div>
        </div>
        <div className="proto-chips">
          <button className="proto-chip on">全部</button>
          {['① 侦察', '② 武器化', '③ 投递', '④ 利用', '⑤ 控制', '⑥ 横向', '⑦ 行动'].map((c) => (
            <button key={c} className="proto-chip">{c}</button>
          ))}
        </div>
        <div className="proto-tool-grid">
          {[
            { name: 'nmap', desc: '端口扫描与服务版本识别，支持脚本引擎 NSE。', stage: '① 侦察', status: 'soft-ok', label: '已安装' },
            { name: 'gobuster', desc: '目录与子域名暴力枚举，自动适配响应过滤。', stage: '① 侦察', status: 'soft-ok', label: '已安装' },
            { name: 'hydra', desc: '多协议并行登录爆破，凭据智能生成。', stage: '④ 利用', status: 'soft-med', label: 'T2 审批' },
            { name: 'sqlmap', desc: 'SQL 注入自动检测与数据脱敏导出。', stage: '④ 利用', status: 'soft-ok', label: '已安装' },
            { name: 'metasploit', desc: '漏洞利用框架，调用需逐次 T3 审批。', stage: '④ 利用', status: 'soft-med', label: 'T3 审批' },
            { name: 'crackmapexec', desc: '内网凭据喷洒与存活验证（SMB/LDAP/WinRM）。', stage: '⑥ 横向', status: 'soft-ok', label: '已安装' },
          ].map((t, i) => (
            <div key={i} className="proto-tool-card">
              <div className="proto-tc-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
              </div>
              <div className="proto-tc-name">{t.name}</div>
              <div className="proto-tc-desc">{t.desc}</div>
              <div className="proto-tc-foot"><span className="proto-kc-badge">{t.stage}</span><span className={'proto-badge ' + t.status}>{t.label}</span></div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}