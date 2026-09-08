// SkillScreen：技能视图。
import type { DBShape, Task } from '../db.ts'

export function SkillScreen({ currentTask: _currentTask }: { db: DBShape; currentTask: Task | null }) {
  return (
    <div className="proto-screen on">
      <div className="proto-page">
        <div className="proto-page-head">
          <span className="proto-page-title">技能</span>
          <span className="proto-page-sub">智能体的可编排能力 · 由触发词激活</span>
        </div>
        <div className="proto-chips">
          <button className="proto-chip on">全部</button>
          <button className="proto-chip">侦察</button>
          <button className="proto-chip">利用</button>
          <button className="proto-chip">后渗透</button>
          <button className="proto-chip">报告</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
          {[
            { name: 'Web 指纹识别', desc: '综合响应头、Cookie、静态资源哈希识别 Web 技术栈与版本。', pills: ['识别指纹', 'what is this site', '技术栈'] },
            { name: 'CVE 情报关联', desc: '将扫描到的服务版本与本地 CVE 库自动关联，评估可利用性。', pills: ['查 CVE', '有没有漏洞'] },
            { name: '凭据智能生成', desc: '基于目标组织信息的弱口令字典定制（年份/域名/品牌变体）。', pills: ['生成字典', '猜密码'] },
            { name: '内网横向评估', desc: '以单点凭据评估可达范围，输出横向移动路径图。', pills: ['能去哪', '横向移动', '评估影响面'] },
            { name: '证据链固化', desc: '每个验证动作自动留存请求/响应/时间戳，形成可法庭采信证据。', pills: ['留证据', '证据链'] },
            { name: '报告自动撰写', desc: '按 PTES 章节结构汇总发现，中文措辞，一键导出。', pills: ['写报告', '生成报告'] },
          ].map((s, i) => (
            <div key={i} className="proto-skill-card">
              <div className="proto-skill-name">{s.name}</div>
              <div className="proto-skill-desc">{s.desc}</div>
              <div className="proto-trig-row">{s.pills.map((p, j) => <span key={j} className="proto-pill">{p}</span>)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}