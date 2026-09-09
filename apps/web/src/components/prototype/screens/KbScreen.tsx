// KbScreen：知识库三栏视图（分类切换联动中栏 + 中栏条目切换右栏文章 + wikilink 回调）。
import { useState } from 'react'
import type { DBShape, Task } from '../db.ts'

interface Article {
  cat: string
  t: string
  m: string
  body: { tag: 'h1' | 'h2' | 'p'; text: string }[]
}

const ARTICLES: Article[] = [
  {
    cat: '方法论',
    t: 'Kill Chain 与 PTES 的融合实践',
    m: '方法论 · 09-06 · 来自任务复盘',
    body: [
      { tag: 'h1', text: 'Kill Chain 与 PTES 的融合实践' },
      { tag: 'p', text: '方法论 · 更新于 2026-09-06 · 来源：ENG-20260901-0031 复盘 · 4 个反向链接' },
      { tag: 'h2', text: '为什么需要融合' },
      { tag: 'p', text: '单纯按 Kill Chain 推进容易陷入「为扫而扫」；单纯按 PTES 七阶段又缺乏对攻击者视角的敬畏。破阵的实践是以 Kill Chain 作为横向推进轴、PTES 作为纵向质量轴：每个 Kill Chain 阶段结束时强制对照 PTES 的对应交付物检查表。' },
      { tag: 'h2', text: '阶段门检查表' },
      { tag: 'p', text: '例如「④ 利用」阶段的完成条件包括：证据链固化、影响面评估、回滚确认。相关设计见【审批分级 T1-T4 设计原则】。' },
      { tag: 'p', text: '在实践中我们发现，【证据留存】的合规性往往比漏洞数量更能决定报告的专业度。' },
    ],
  },
  {
    cat: '方法论',
    t: '审批分级 T1-T4 设计原则',
    m: '方法论 · 09-04 · 手动创建',
    body: [
      { tag: 'h1', text: '审批分级 T1-T4 设计原则' },
      { tag: 'p', text: '方法论 · 更新于 2026-09-04 · 来源：手动创建 · 2 个反向链接' },
      { tag: 'h2', text: '分级标准' },
      { tag: 'p', text: 'T1 只读无副作用 → T2 可逆审批 → T3 高危每调用人工 → T4 禁止。' },
    ],
  },
  {
    cat: '方法论',
    t: '证据留存的法律边界',
    m: '方法论 · 09-01 · 来自任务复盘',
    body: [
      { tag: 'h1', text: '证据留存的法律边界' },
      { tag: 'p', text: '方法论 · 09-01 · 来自任务复盘' },
      { tag: 'h2', text: '授权与留存' },
      { tag: 'p', text: '每个证据条目必须可追溯到具体的授权窗口与目标；超范围留存即违规。' },
    ],
  },
  {
    cat: '方法论',
    t: '指纹去重的增量策略',
    m: '方法论 · 08-28 · 智能体沉淀',
    body: [
      { tag: 'h1', text: '指纹去重的增量策略' },
      { tag: 'p', text: '方法论 · 08-28 · 智能体沉淀' },
      { tag: 'p', text: '资产指纹按 (host, port, banner_hash) 三元组去重；时间窗保留最新 90 天。' },
    ],
  },
]

const CATS: Array<{ name: string; count: number }> = [
  { name: '方法论', count: 12 },
  { name: '漏洞情报', count: 28 },
  { name: '工具手册', count: 19 },
  { name: '复盘记录', count: 7 },
  { name: '环境笔记', count: 4 },
]

export function KbScreen({ currentTask: _currentTask, onToast }: { db: DBShape; currentTask: Task | null; onToast?: (msg: string, kind?: 'ok' | 'err') => void }) {
  const [catIdx, setCatIdx] = useState(0)
  const [itemIdx, setItemIdx] = useState(0)
  const cat = CATS[catIdx]?.name ?? '方法论'
  const items = ARTICLES.filter((a) => a.cat === cat)
  const cur = items[itemIdx] ?? items[0] ?? ARTICLES[0]
  if (cur !== undefined && items.findIndex((a) => a.t === cur.t) === -1 && itemIdx > 0) {
    // 切分类后索引越界，重置
    setItemIdx(0)
  }
  return (
    <div className="proto-screen on" style={{ overflow: 'hidden' }}>
      <div className="proto-kb">
        <div className="proto-kb-col">
          <div className="proto-kb-cat head">分类</div>
          {CATS.map((c, i) => (
            <div
              key={c.name}
              className={'proto-kb-cat' + (i === catIdx ? ' on' : '')}
              onClick={() => { setCatIdx(i); setItemIdx(0) }}
            >
              {c.name} ({c.count})
            </div>
          ))}
        </div>
        <div className="proto-kb-col">
          <div style={{ padding: '16px 18px 10px', display: 'flex', gap: 8, alignItems: 'center', borderBottom: '1px solid var(--border)' }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ color: 'var(--text-3)' }}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input placeholder="搜索条目…" style={{ border: 'none', outline: 'none', background: 'none', fontSize: 12.5, flex: 1, color: 'var(--text-1)' }} />
          </div>
          {items.map((it, i) => (
            <div
              key={it.t}
              className={'proto-kb-item' + (i === itemIdx ? ' on' : '')}
              onClick={() => setItemIdx(i)}
            >
              <div className="proto-kb-t">{it.t}</div>
              <div className="proto-kb-m">{it.m}</div>
            </div>
          ))}
        </div>
        <div className="proto-kb-col">
          <div className="proto-kb-read">
            {cur ? cur.body.map((b, i) => {
              if (b.tag === 'h1') return <h1 key={i}>{b.text}</h1>
              if (b.tag === 'h2') return <h2 key={i}>{b.text}</h2>
              // p 段落：内联【…】→ wikilink 按钮
              const parts = b.text.split(/【([^】]+)】/)
              return (
                <p key={i}>
                  {parts.map((seg, j) => (j % 2 === 1 ? (
                    <button
                      key={j}
                      type="button"
                      className="wikilink"
                      onClick={() => onToast?.(`跳转：${seg}`, 'ok')}
                    >
                      {seg}
                    </button>
                  ) : (
                    <span key={j}>{seg}</span>
                  )))}
                </p>
              )
            }) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
