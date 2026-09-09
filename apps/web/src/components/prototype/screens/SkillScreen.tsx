// SkillScreen：技能视图。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

interface Skill {
  name: string
  desc: string
  cat: '侦察' | '利用' | '后渗透' | '报告'
  pills: string[]
}

const SKILLS: Skill[] = [
  { name: 'Web 指纹识别', desc: '综合响应头、Cookie、静态资源哈希识别 Web 技术栈与版本。', cat: '侦察', pills: ['识别指纹', 'what is this site', '技术栈'] },
  { name: 'CVE 情报关联', desc: '将扫描到的服务版本与本地 CVE 库自动关联，评估可利用性。', cat: '侦察', pills: ['查 CVE', '有没有漏洞'] },
  { name: '凭据智能生成', desc: '基于目标组织信息的弱口令字典定制（年份/域名/品牌变体）。', cat: '侦察', pills: ['生成字典', '猜密码'] },
  { name: '内网横向评估', desc: '以单点凭据评估可达范围，输出横向移动路径图。', cat: '后渗透', pills: ['能去哪', '横向移动', '评估影响面'] },
  { name: '证据链固化', desc: '每个验证动作自动留存请求/响应/时间戳，形成可法庭采信证据。', cat: '后渗透', pills: ['留证据', '证据链'] },
  { name: '报告自动撰写', desc: '按 PTES 章节结构汇总发现，中文措辞，一键导出。', cat: '报告', pills: ['写报告', '生成报告'] },
  { name: 'SQL 注入探测', desc: '自动探测 GET/POST 参数的 SQL 注入并分类盲注/报错/联合。', cat: '利用', pills: ['sql 注入', 'sqli'] },
  { name: 'XSS Payload 库', desc: '内置 200+ 经过编码绕过的 XSS payload，按浏览器自动选型。', cat: '利用', pills: ['xss', '弹个窗'] },
]

const CATS: Array<Skill['cat'] | '全部'> = ['全部', '侦察', '利用', '后渗透', '报告']

export function SkillScreen({ currentTask: _currentTask, onPillActivate }: { db: DBShape; currentTask: Task | null; onPillActivate?: (text: string) => void }) {
  const [cat, setCat] = useState<Skill['cat'] | '全部'>('全部')
  const filtered = cat === '全部' ? SKILLS : SKILLS.filter((s) => s.cat === cat)
  return (
    <div className="proto-screen on">
      <div className="proto-page">
        <div className="proto-page-head">
          <span className="proto-page-title">技能</span>
          <span className="proto-page-sub">智能体的可编排能力 · 由触发词激活</span>
        </div>
        <div className="proto-chips">
          {CATS.map((c) => (
            <button
              key={c}
              type="button"
              className={'proto-chip' + (c === cat ? ' on' : '')}
              onClick={() => setCat(c)}
            >
              {c}
            </button>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
          {filtered.map((s, i) => (
            <div key={i} className="proto-skill-card">
              <div className="proto-skill-name">{s.name}</div>
              <div className="proto-skill-desc">{s.desc}</div>
              <div className="proto-trig-row">
                {s.pills.map((p, j) => (
                  <button
                    key={j}
                    type="button"
                    className="proto-pill"
                    title="在对话中激活此触发词"
                    onClick={() => onPillActivate?.(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
