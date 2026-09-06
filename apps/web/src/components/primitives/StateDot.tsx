// StateDot：目标/工具状态指示（复刻 dsh ui-primitives StateDot）。
// done/warning/error：10x10 同色 10% 光晕 + 6x6 实心核；ongoing：3x3 像素
// 矩阵顺时针追逐（flat keyframe 阶梯，复古感）。颜色全部走 --secai-* token。

import clsx from 'clsx'
import css from './StateDot.module.css'

/** 四态语义：绿 done / 琥珀 warning / 蓝 ongoing / 红 error。 */
export type StateDotState = 'done' | 'warning' | 'ongoing' | 'error'

/** 外圈 3x3 矩阵格（10px 网格上的 2px 像素），自左上角顺时针。 */
const MATRIX_CELLS: readonly (readonly [number, number])[] = [
  [0, 0], [4, 0], [8, 0], [8, 4], [8, 8], [4, 8], [0, 8], [0, 4],
]

/**
 * 渲染一个状态圆点。
 * @param state - 四种状态之一。
 * @param size - 外径 px（默认 10，figma 尺寸）。
 * @param className - 布局附加类。
 */
export function StateDot({ state, size = 10, className }: {
  state: StateDotState
  size?: number | undefined
  className?: string | undefined
}) {
  if (state === 'ongoing') {
    return (
      <svg
        className={clsx(css.matrix, className)}
        data-state="ongoing"
        width={size}
        height={size}
        viewBox="0 0 10 10"
        shapeRendering="crispEdges"
        aria-hidden="true"
      >
        {MATRIX_CELLS.map(([x, y], index) => (
          <rect
            key={`${x}-${y}`}
            className={css.cell}
            x={x}
            y={y}
            width="2"
            height="2"
            /* 负延迟相位让追逐从挂载起每格连续点亮 */
            style={{ animationDelay: `${(index - MATRIX_CELLS.length) * 125}ms` }}
          />
        ))}
      </svg>
    )
  }
  return (
    <span
      className={clsx(css.dot, className)}
      data-state={state}
      style={{ width: size, height: size }}
      aria-hidden="true"
    />
  )
}
