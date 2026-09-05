/**
 * F2 三栏让步链求解器（纯函数，参照 dsh ui-layout columns.ts）。
 *
 * 让步链固定顺序：中心栏 >= CENTER_MIN 优先 —— 视口不足先压缩 details 到其
 * 最小值，仍不足则自动收拢 details（推导宽为 0，绝不改写拖拽偏好，视口回宽
 * 即自动还原）；sidebar 永不让步（渲染宽 = 拖拽偏好）；剩余缺口最后全由中心
 * 栏吸收。输入是布局 store 的普通宽度偏好（px，0 = 关闭），输出是渲染决议，
 * 因此无迟滞：收窄-回宽是自动可逆的。
 */

/** 本次渲染的三栏决议宽。 */
export interface Columns {
  sidebar: number
  center: number
  details: number
}

/** 中心栏下限；仅让步链末级兜底允许跌破。 */
export const CENTER_MIN = 520
/** sidebar 拖拽下限。 */
export const SIDEBAR_MIN = 160
/** sidebar 拖拽上限。 */
export const SIDEBAR_MAX = 400
/** sidebar 默认宽（用户拖拽前）。 */
export const SIDEBAR_DEFAULT = 260
/** details 拖拽下限。 */
export const DETAILS_MIN = 240
/** details 拖拽上限。 */
export const DETAILS_MAX = 560
/** details 默认宽（用户拖拽前）。 */
export const DETAILS_DEFAULT = 320

/** 把面板宽夹紧到契约范围（偏好跨 store 边界回传时可能越界，须重夹）。 */
export function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(px)))
}

/** 求解一个视口下的三栏宽；纯函数，(viewport, 偏好) → 决议。 */
export function computeColumns(viewport: number, sidebar: number, details: number): Columns {
  const s = sidebar === 0 ? 0 : clampWidth(sidebar, SIDEBAR_MIN, SIDEBAR_MAX)
  const d0 = details === 0 ? 0 : clampWidth(details, DETAILS_MIN, DETAILS_MAX)

  // 第 1 步：首选宽度全放得下。
  if (s + d0 + CENTER_MIN <= viewport) return { sidebar: s, center: viewport - s - d0, details: d0 }

  // 第 2 步：压缩 details 到其最小值。
  const d1 = d0 === 0 ? 0 : Math.max(DETAILS_MIN, viewport - s - CENTER_MIN)
  if (s + d1 + CENTER_MIN <= viewport) return { sidebar: s, center: CENTER_MIN, details: d1 }

  // 第 3 步：自动收拢 details（推导宽为 0，偏好未动）；中心栏吸收缺口
  // （末级兜底，可跌破 CENTER_MIN）。
  return { sidebar: s, center: Math.max(0, viewport - s), details: 0 }
}
