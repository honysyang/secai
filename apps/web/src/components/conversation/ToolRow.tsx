// ToolRow：工具轨迹单行（复刻 dsh ui-tool ToolRow，去掉卡片族变体）。
// 24px 单行 [16px 前导槽: 状态点/图标] gap6 [title 14/24] gap8 [2x2 分隔圆点]
// gap8 [summary FILL 截断]；整行 = 展开开关。running 态扫光带（color-mix
// bg-base 60%，2.6s ease-out 循环 + 10% 收尾停顿）；error 态前导红点 +
// 摘要换失败首行（error 色）。展开体 = IN/OUT gutter-label 卡片
// （markdown-code-block 底、12px 圆角、各段 max-height 150px 独立滚动）。

import { useState } from 'react'
import { DisclosureRow } from '../primitives/DisclosureRow.tsx'
import { StateDot } from '../primitives/StateDot.tsx'
import css from './ToolRow.module.css'

/** 行状态：ok 完成 / running 进行中 / error 失败。 */
export type ToolRowState = 'ok' | 'running' | 'error'

export interface ToolRowProps {
  /** 工具名（title）。 */
  title: string
  /** 折叠态摘要（参数一句话）。 */
  summary: string
  /** 失败首行（error 态替换摘要显示）。 */
  errorSummary?: string | null | undefined
  /** 展开体输入（IN）文本；null = 无输入段。 */
  body: string | null
  /** 展开体输出（OUT）文本；null = 无输出段。 */
  output?: string | null | undefined
  state: ToolRowState
  /** 前导 16px 图标（默认小扳手轮廓）；running/error 态由 StateDot 顶替。 */
  icon?: React.ReactNode
}

/** 前导槽状态顶替：error = 红点，running = 蓝追逐点，其余用传入图标。 */
function leadingFor(state: ToolRowState, icon: React.ReactNode): React.ReactNode {
  switch (state) {
    case 'error':
      return <StateDot state="error" size={10} />
    case 'running':
      return <StateDot state="ongoing" size={10} />
    default:
      return icon
  }
}

/** 行状态的无障碍文案（StateDot 与扫光带都是 aria-hidden / 纯视觉）。 */
function stateStatus(state: ToolRowState): string | null {
  switch (state) {
    case 'running':
      return '运行中'
    case 'error':
      return '失败'
    default:
      return null
  }
}

function WrenchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11.2 3.2a3.4 3.4 0 0 0-4.5 4.5L2.6 12a1.9 1.9 0 1 0 2.7 2.7l4.2-4.1a3.4 3.4 0 0 0 4.6-4.6l-2.6 2.6-2.1-.5-.5-2.1 2.3-1.8Z" />
    </svg>
  )
}

export function ToolRow({
  title,
  summary,
  errorSummary,
  body,
  output,
  state,
  icon,
}: ToolRowProps) {
  const [expanded, setExpanded] = useState(false)
  const outputText = output ?? null
  const expandable = body !== null || outputText !== null
  const open = expanded && expandable
  const status = stateStatus(state)
  // error 行的折叠摘要 = 失败首行（error 色），替换参数摘要。
  const failureLine = state === 'error' ? (errorSummary ?? null) : null
  const summaryText = failureLine ?? summary
  const inputText = body

  return (
    <div className={css.root} data-state={state}>
      {status !== null && <span className={css.visuallyHidden}>{status}</span>}
      <DisclosureRow
        rowClassName={css.row}
        leadingClassName={css.leading}
        titleClassName={css.title}
        icon={leadingFor(state, icon ?? <WrenchIcon />)}
        title={title}
        open={open}
        expandable={expandable}
        expandOnRowClick
        keepContentWhenOpen
        onToggle={() => setExpanded(v => !v)}
        collapsedContent={summaryText !== '' && (
          <>
            <span className={css.sep} aria-hidden="true" />
            <span className={`${css.summary}${failureLine !== null ? ` ${css.errorSummary}` : ''}`}>
              {summaryText}
            </span>
          </>
        )}
      >
        {/* 展开体（DisclosureRow body 的兄弟节点，点击不切换行） */}
        <div className={css.bodyWrap}>
          {(inputText !== null || outputText !== null) && (
            <div className={css.ioCard}>
              {inputText !== null && (
                <div className={css.ioSection}>
                  <span className={css.ioLabel}>IN</span>
                  <span className={css.ioText}>{inputText}</span>
                </div>
              )}
              {inputText !== null && outputText !== null && (
                <span className={css.ioDivider} aria-hidden="true" />
              )}
              {outputText !== null && (
                <div className={css.ioSection}>
                  <span className={css.ioLabel}>OUT</span>
                  <span className={css.ioText} data-error={state === 'error' || undefined}>
                    {outputText}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </DisclosureRow>
    </div>
  )
}
