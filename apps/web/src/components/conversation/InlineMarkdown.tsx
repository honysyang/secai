/** 极简 Markdown 行内渲染器：粗体 / 内联代码 / 斜体（不引入外部依赖）。 */

import type { ReactNode } from 'react'
import css from './InlineMarkdown.module.css'

/** 渲染行内 markdown（**粗体**、`代码`、*斜体*）。 */
export function renderInlineMarkdown(text: string): ReactNode[] {
  // 按 **bold** / `code` / *italic* 切分
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g
  const parts: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  let key = 0

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    const raw = match[0]
    if (raw.startsWith('**')) {
      parts.push(<strong key={key++} className={css.bold}>{raw.slice(2, -2)}</strong>)
    } else if (raw.startsWith('`')) {
      parts.push(<code key={key++} className={css.code}>{raw.slice(1, -1)}</code>)
    } else if (raw.startsWith('*')) {
      parts.push(<em key={key++} className={css.italic}>{raw.slice(1, -1)}</em>)
    }
    last = match.index + raw.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}
