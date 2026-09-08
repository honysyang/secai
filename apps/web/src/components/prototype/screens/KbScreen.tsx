// KbScreen：知识库三栏视图。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

export function KbScreen({ currentTask: _currentTask }: { db: DBShape; currentTask: Task | null }) {
  const [active, setActive] = useState(0)
  const cats = ['方法论 (12)', '漏洞情报 (28)', '工具手册 (19)', '复盘记录 (7)', '环境笔记 (4)']
  const items = [
    { t: 'Kill Chain 与 PTES 的融合实践', m: '方法论 · 09-06 · 来自任务复盘' },
    { t: '审批分级 T1-T4 设计原则', m: '方法论 · 09-04 · 手动创建' },
    { t: '证据留存的法律边界', m: '方法论 · 09-01 · 来自任务复盘' },
    { t: '指纹去重的增量策略', m: '方法论 · 08-28 · 智能体沉淀' },
  ]
  return (
    <div className="proto-screen on" style={{ overflow: 'hidden' }}>
      <div className="proto-kb">
        <div className="proto-kb-col">
          <div className="proto-kb-cat head">分类</div>
          {cats.map((c, i) => (
            <div key={i} className={'proto-kb-cat' + (i === active ? ' on' : '')} onClick={() => setActive(i)}>{c}</div>
          ))}
        </div>
        <div className="proto-kb-col">
          <div style={{ padding: '16px 18px 10px', display: 'flex', gap: 8, alignItems: 'center', borderBottom: '1px solid var(--border)' }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--text-3)' }}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input placeholder="搜索条目…" style={{ border: 'none', outline: 'none', background: 'none', fontSize: 12.5, flex: 1, color: 'var(--text-1)' }} />
          </div>
          {items.map((it, i) => (
            <div key={i} className={'proto-kb-item' + (i === 0 ? ' on' : '')}>
              <div className="proto-kb-t">{it.t}</div>
              <div className="proto-kb-m">{it.m}</div>
            </div>
          ))}
        </div>
        <div className="proto-kb-col">
          <div className="proto-kb-read">
            <h1>Kill Chain 与 PTES 的融合实践</h1>
            <div className="meta">方法论 · 更新于 2026-09-06 · 来源：ENG-20260901-0031 复盘 · 4 个反向链接</div>
            <h2>为什么需要融合</h2>
            <p>单纯按 <code>Kill Chain</code> 推进容易陷入「为扫而扫」；单纯按 <code>PTES</code> 七阶段又缺乏对攻击者视角的敬畏。破阵的实践是以 Kill Chain 作为<strong>横向推进轴</strong>、PTES 作为<strong>纵向质量轴</strong>：每个 Kill Chain 阶段结束时强制对照 PTES 的对应交付物检查表。</p>
            <h2>阶段门检查表</h2>
            <p>例如「④ 利用」阶段的完成条件包括：证据链固化、影响面评估、回滚确认。相关设计见<span className="wikilink">审批分级 T1-T4 设计原则</span>。</p>
            <p>在实践中我们发现，<span className="wikilink">证据留存</span>的合规性往往比漏洞数量更能决定报告的专业度。</p>
          </div>
        </div>
      </div>
    </div>
  )
}