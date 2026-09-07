/**
 * SkillsPanel：Skills 管理视图（清单 + 分类筛选 + 详情预览）。
 * 数据 = /api/skills（AppSnapshot.skills 为清单摘要）。
 * 结构 = 头部（标题 + 搜索框）+ 分类 chips + 技能卡片网格。
 */

import { useMemo, useState } from 'react'
import type { SkillSummary } from '../../connection/api.ts'
import { EmptyState } from '../primitives/EmptyState.tsx'
import css from './SkillsPanel.module.css'

export interface SkillsPanelProps {
  skills: readonly SkillSummary[]
}

export function SkillsPanel({ skills }: SkillsPanelProps) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<string>('all')

  const categories = useMemo(() => {
    const counts = new Map<string, number>()
    for (const skill of skills) {
      const key = skill.category || '通用'
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [skills])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return skills.filter((skill) => {
      if (category !== 'all' && (skill.category || '通用') !== category) return false
      if (q === '') return true
      return skill.name.toLowerCase().includes(q) || skill.description.toLowerCase().includes(q)
    })
  }, [skills, query, category])

  return (
    <div className={css.root}>
      <div className={css.header}>
        <h1 className={css.title}>Skills</h1>
        <span className={css.subtitle}>渗透技能库管理（{skills.length} 个技能）</span>
        <div className={css.spacer} />
        <input
          type="search"
          className={css.search}
          placeholder="搜索技能…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className={css.categoryChips}>
        <button
          type="button"
          className={`${css.chip} ${category === 'all' ? css.chipActive : ''}`}
          onClick={() => setCategory('all')}
        >
          全部 ({skills.length})
        </button>
        {categories.map(([cat, count]) => (
          <button
            key={cat}
            type="button"
            className={`${css.chip} ${category === cat ? css.chipActive : ''}`}
            onClick={() => setCategory(cat)}
          >
            {cat} ({count})
          </button>
        ))}
      </div>

      <div className={css.grid}>
        {filtered.length === 0 ? (
          <EmptyState
            title="无匹配技能"
            description="调整搜索或分类筛选条件。"
          />
        ) : (
          filtered.map((skill) => (
            <div key={skill.name} className={css.card}>
              <div className={css.cardHeader}>
                <span className={css.cardName}>{skill.name}</span>
                {skill.category && <span className={css.cardCategory}>{skill.category}</span>}
              </div>
              <p className={css.cardDesc}>{skill.description}</p>
              {skill.triggers.length > 0 && (
                <div className={css.cardTriggers}>
                  {skill.triggers.slice(0, 4).map((trigger) => (
                    <span key={trigger} className={css.trigger}>{trigger}</span>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
