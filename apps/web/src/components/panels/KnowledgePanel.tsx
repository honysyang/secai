/**
 * KnowledgePanel：知识库管理视图（清单 + 搜索 + 详情预览）。
 * 数据 = /api/knowledge（AppSnapshot.knowledge 为清单摘要）。
 * 结构 = 头部（标题 + 搜索框）+ 知识列表（左侧）+ 详情面板（右侧）。
 */

import { useMemo, useState } from 'react'
import type { KnowledgeSummary } from '../../connection/api.ts'
import { EmptyState } from '../primitives/EmptyState.tsx'
import css from './KnowledgePanel.module.css'

export interface KnowledgePanelProps {
  knowledge: readonly KnowledgeSummary[]
}

export function KnowledgePanel({ knowledge }: KnowledgePanelProps) {
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q === '') return knowledge
    return knowledge.filter(
      (item) => item.id.toLowerCase().includes(q) || item.desc.toLowerCase().includes(q),
    )
  }, [knowledge, query])

  const selected = knowledge.find((item) => item.id === selectedId) ?? null

  return (
    <div className={css.root}>
      <div className={css.header}>
        <h1 className={css.title}>知识库</h1>
        <span className={css.subtitle}>安全知识条目管理（{knowledge.length} 条）</span>
        <div className={css.spacer} />
        <input
          type="search"
          className={css.search}
          placeholder="搜索知识…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className={css.body}>
        <div className={css.list}>
          {filtered.length === 0 ? (
            <EmptyState
              title="无匹配知识"
              description="调整搜索条件。"
            />
          ) : (
            filtered.map((item) => (
              <div
                key={item.id}
                className={css.item}
                data-selected={selectedId === item.id || undefined}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedId(item.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setSelectedId(item.id)
                  }
                }}
              >
                <span className={css.itemId}>{item.id}</span>
                <span className={css.itemDesc}>{item.desc}</span>
              </div>
            ))
          )}
        </div>

        <div className={css.detail}>
          {selected === null ? (
            <div className={css.detailEmpty}>选择左侧条目查看详情。</div>
          ) : (
            <>
              <h2 className={css.detailTitle}>{selected.id}</h2>
              <p className={css.detailDesc}>{selected.desc}</p>
              <pre className={css.detailBody}>{selected.desc}</pre>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
