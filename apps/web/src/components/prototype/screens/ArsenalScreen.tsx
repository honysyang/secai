// ArsenalScreen：武器库视图（36 工具 × Kill Chain 7 阶段）。
// 对齐 prototype.html v5：每张工具卡独立 SVG icon + 完整 7 阶段 chip 切换 + hover 微浮。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

type ToolStatus = 'soft-ok' | 'soft-med' | 'soft-high' | 'soft-crit'
type KillChain = '① 侦察' | '② 武器化' | '③ 投递' | '④ 利用' | '⑤ 控制' | '⑥ 横向' | '⑦ 行动'

interface Tool {
  name: string
  desc: string
  stage: KillChain
  status: ToolStatus
  label: string
  icon: string
}

const TOOLS: Tool[] = [
  { name: 'nmap', desc: '端口扫描与服务版本识别，支持脚本引擎 NSE。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M3 12h2l3-9 4 18 3-9h6' },
  { name: 'masscan', desc: '极速端口扫描（百万级端口/秒），适合大网段。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M2 12h4M18 12h4M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8' },
  { name: 'gobuster', desc: '目录与子域名暴力枚举，自动适配响应过滤。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M3 6h18M6 6v14M18 6v14M3 20h18M9 12h6M9 16h6' },
  { name: 'amass', desc: '被动 + 主动子域枚举，集成多数据源。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20' },
  { name: 'subfinder', desc: '被动子域发现，纯在线数据源拼装。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M21 21l-4.3-4.3M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM10 8v4M10 14h.01' },
  { name: 'dnsx', desc: 'DNS 探测与爆破，多 resolver 并行。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M12 2v20M5 9l7-7 7 7M5 15l7 7 7-7' },
  { name: 'httpx', desc: 'HTTP 探针 + 标题/状态码/技术栈指纹。', stage: '① 侦察', status: 'soft-ok', label: '已安装', icon: 'M3 12h18M12 3v18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4' },
  { name: 'msfvenom', desc: 'Metasploit 载荷生成器，支持多编码与模板。', stage: '② 武器化', status: 'soft-med', label: 'T2 审批', icon: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 13h6M9 17h6' },
  { name: 'shellcode_encoder', desc: '自定义 shellcode 编码器，绕过 AV/EDR。', stage: '② 武器化', status: 'soft-med', label: 'T2 审批', icon: 'M8 2l3 3h2L10 2zM2 8h20M6 6h12v14H6zM9 11h6M9 15h4' },
  { name: 'veil', desc: 'AV 绕过载荷生成框架。', stage: '② 武器化', status: 'soft-med', label: 'T2 审批', icon: 'M12 2a8 8 0 0 0-8 8c0 6 8 12 8 12s8-6 8-12a8 8 0 0 0-8-8zM12 8v4M12 16h.01' },
  { name: 'unicorn', desc: '跨架构 shellcode 注入（Python 嵌入）。', stage: '② 武器化', status: 'soft-med', label: 'T2 审批', icon: 'M12 2 4 7v10l8 5 8-5V7zM12 12l-8-5M12 12l8-5M12 12v10' },
  { name: 'donut', desc: '.NET 程序集原生 shellcode 生成。', stage: '② 武器化', status: 'soft-med', label: 'T2 审批', icon: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 6v6l4 2' },
  { name: 'gophish', desc: '钓鱼演练平台（开源）。', stage: '③ 投递', status: 'soft-high', label: 'T3 审批', icon: 'M3 7l9 6 9-6M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7M3 7l9-5 9 5' },
  { name: 'evilginx2', desc: '高级钓鱼中间人框架（绕过 2FA）。', stage: '③ 投递', status: 'soft-high', label: 'T3 审批', icon: 'M3 11h18M5 11V7a3 3 0 0 1 6 0v4M13 11V7a3 3 0 0 1 6 0v4M3 11v8a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-8' },
  { name: 'macro_pack', desc: 'Office 宏载荷生成与混淆。', stage: '③ 投递', status: 'soft-high', label: 'T3 审批', icon: 'M3 4h18v6H3zM3 14h18v6H3zM7 7h.01M7 17h.01' },
  { name: 'swaks', desc: 'SMTP 测试/投递瑞士军刀。', stage: '③ 投递', status: 'soft-ok', label: '已安装', icon: 'M2 6l10 7 10-7M2 6v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6' },
  { name: 'metasploit', desc: '漏洞利用框架，调用需逐次 T3 审批。', stage: '④ 利用', status: 'soft-high', label: 'T3 审批', icon: 'M12 2L4 7v10l8 5 8-5V7zM12 2v20M4 7l16 10M20 7 4 17' },
  { name: 'sqlmap', desc: 'SQL 注入自动检测与数据脱敏导出。', stage: '④ 利用', status: 'soft-ok', label: '已安装', icon: 'M13 2L3 14h7l-1 8 10-12h-7z' },
  { name: 'hydra', desc: '多协议并行登录爆破，凭据智能生成。', stage: '④ 利用', status: 'soft-med', label: 'T2 审批', icon: 'M5 11V7a5 5 0 0 1 10 0v4M3 11h14v10H3zM12 17v.01' },
  { name: 'burpsuite', desc: 'Web 漏洞扫描与拦截代理（社区版）。', stage: '④ 利用', status: 'soft-ok', label: '已安装', icon: 'M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0zM12 8v4l3 2' },
  { name: 'rsa_sign2n', desc: 'RSA 签名伪造（CTF 利用）。', stage: '④ 利用', status: 'soft-high', label: 'T3 审批', icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM8 12l3 3 5-6' },
  { name: 'commix', desc: '命令注入自动化利用。', stage: '④ 利用', status: 'soft-high', label: 'T3 审批', icon: 'M4 4h16v4H4zM4 10h16M4 14h16v6H4zM8 16h.01' },
  { name: 'jndi_exploit', desc: 'JNDI 注入载荷服务（Log4Shell 等）。', stage: '④ 利用', status: 'soft-high', label: 'T3 审批', icon: 'M5 12h14M12 5v14M8 8l8 8M16 8l-8 8' },
  { name: 'ysoserial', desc: 'Java 反序列化利用工具。', stage: '④ 利用', status: 'soft-high', label: 'T3 审批', icon: 'M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9 4.9 19.1' },
  { name: 'sliver', desc: '跨平台 C2 框架（开源）。', stage: '⑤ 控制', status: 'soft-high', label: 'T3 审批', icon: 'M12 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM4 22v-3a8 8 0 0 1 16 0v3M4 16h16' },
  { name: 'mythic', desc: '模块化 C2 框架（多 payload）。', stage: '⑤ 控制', status: 'soft-high', label: 'T3 审批', icon: 'M3 12h2l3-9 4 18 3-9h6' },
  { name: 'covenant', desc: '.NET C2 框架。', stage: '⑤ 控制', status: 'soft-high', label: 'T3 审批', icon: 'M2 12h20M12 2v20' },
  { name: 'empire', desc: 'PowerShell + Python C2。', stage: '⑤ 控制', status: 'soft-high', label: 'T3 审批', icon: 'M12 2l9 5v10l-9 5-9-5V7zM12 12L3 7M12 12l9-5M12 12v10' },
  { name: 'chisel', desc: 'TCP/UDP 隧道穿透工具。', stage: '⑤ 控制', status: 'soft-ok', label: '已安装', icon: 'M3 6h18M3 12h18M3 18h18' },
  { name: 'crackmapexec', desc: '内网凭据喷洒与存活验证（SMB/LDAP/WinRM）。', stage: '⑥ 横向', status: 'soft-ok', label: '已安装', icon: 'M3 5h7v7H3zM14 5h7v7h-7zM3 14h7v5H3zM14 14h7v5h-7z' },
  { name: 'impacket', desc: 'Python 协议实现库（含 psexec/wmiexec）。', stage: '⑥ 横向', status: 'soft-ok', label: '已安装', icon: 'M3 4h18v6H3zM3 14h18v6H3zM7 7h.01M7 17h.01' },
  { name: 'kerbrute', desc: 'Kerberos 暴力枚举工具。', stage: '⑥ 横向', status: 'soft-med', label: 'T2 审批', icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20' },
  { name: 'bloodhound', desc: 'AD 攻击路径图谱化工具。', stage: '⑥ 横向', status: 'soft-ok', label: '已安装', icon: 'M3 6l9-4 9 4-9 4zM3 6v6l9 4 9-4V6M3 12v6l9 4 9-4v-6' },
  { name: 'sshuttle', desc: '透明 SSH VPN 代理。', stage: '⑥ 横向', status: 'soft-ok', label: '已安装', icon: 'M2 12h20M12 2v20M4.9 4.9l14.2 14.2' },
  { name: 'mimikatz', desc: 'Windows 凭据提取（哈希/TGT/票据）。', stage: '⑦ 行动', status: 'soft-crit', label: 'T4 禁止', icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 8v8M8 12h8' },
  { name: 'rubeus', desc: 'Kerberos 票据攻击工具集。', stage: '⑦ 行动', status: 'soft-crit', label: 'T4 禁止', icon: 'M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0zM9 9l6 6M15 9l-6 6' },
  { name: 'laZzzy', desc: '进程注入壳（无文件落地）。', stage: '⑦ 行动', status: 'soft-crit', label: 'T4 禁止', icon: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 7v5l3 2' },
  { name: 'scattered_spider', desc: '横向移动剧本集（手动）。', stage: '⑦ 行动', status: 'soft-crit', label: 'T4 禁止', icon: 'M2 12h20M5 5l14 14M19 5 5 19' },
]

const ALL_STAGES: Array<KillChain | '全部'> = ['全部', '① 侦察', '② 武器化', '③ 投递', '④ 利用', '⑤ 控制', '⑥ 横向', '⑦ 行动']

export function ArsenalScreen({ currentTask: _currentTask }: { db: DBShape; currentTask: Task | null }) {
  const [stage, setStage] = useState<KillChain | '全部'>('全部')
  const filtered = stage === '全部' ? TOOLS : TOOLS.filter((t) => t.stage === stage)
  const installed = TOOLS.filter((t) => t.status === 'soft-ok').length
  const installable = TOOLS.filter((t) => t.status === 'soft-med').length
  const t3plus = TOOLS.filter((t) => t.status === 'soft-high' || t.status === 'soft-crit').length

  return (
    <div className="proto-screen on">
      <div className="proto-page">
        <div className="proto-page-head">
          <span className="proto-page-title">武器库</span>
          <span className="proto-page-sub">{TOOLS.length} 个工具 · 按 Kill Chain 阶段编排</span>
        </div>
        <div className="proto-stat-grid">
          <div className="proto-stat-card"><div className="proto-stat-label">已安装</div><div className="proto-stat-num">{installed}</div></div>
          <div className="proto-stat-card"><div className="proto-stat-label">可安装</div><div className="proto-stat-num">{installable}</div></div>
          <div className="proto-stat-card"><div className="proto-stat-label">需授权 (T3+)</div><div className="proto-stat-num" style={{ color: 'var(--med)' }}>{t3plus}</div></div>
        </div>
        <div className="proto-chips">
          {ALL_STAGES.map((c) => (
            <button
              key={c}
              type="button"
              className={'proto-chip' + (c === stage ? ' on' : '')}
              onClick={() => setStage(c)}
            >
              {c}
            </button>
          ))}
        </div>
        <div className="proto-tool-grid">
          {filtered.map((t) => (
            <div key={t.name} className="proto-tool-card">
              <div className="proto-tc-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d={t.icon} />
                </svg>
              </div>
              <div className="proto-tc-name">{t.name}</div>
              <div className="proto-tc-desc">{t.desc}</div>
              <div className="proto-tc-foot">
                <span className="proto-kc-badge">{t.stage}</span>
                <span className={'proto-badge ' + t.status}>{t.label}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
