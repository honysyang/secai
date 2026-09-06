/**
 * 三栏让步链求解器（纯函数，复刻 dsh ui-layout columns.ts 语义）。
 *
 * dsh 三栏合同：sidebar min264/max420/default280（收起成 56px rail）、
 * details min300/max520/default360、center min640（窄视口末级兜底可跌破）。
 * 让步链固定顺序：sidebar 永不让步（渲染宽 = 拖拽偏好）—— 视口不足先压
 * details 到其最小值，仍不足则自动收拢 details（推导宽为 0，绝不改写拖拽
 * 偏好，视口回宽即自动还原）；剩余缺口最后全由中心栏吸收。输入是布局
 * store 的普通宽度偏好（px，0 = 收起），输出是渲染决议，因此无迟滞：
 * 收窄-回宽是自动可逆的。
 */

/** 本次渲染的三栏决议宽。 */
export interface Columns {
  sidebar: number
  center: number
  details: number
}

/** dsh 合同：中心栏下限（末级兜底可跌破）。 */
export const CENTER_MIN = 640
/** 窄屏阈值：低于此宽 sidebar 自动收成 rail（dsh SIDEBAR_AUTO_COLLAPSE）。 */
export const SIDEBAR_AUTO_COLLAPSE = 1024
/** sidebar 收起态 rail 宽（dsh SIDEBAR_COLLAPSED）。 */
export const SIDEBAR_COLLAPSED = 56
/** sidebar 拖拽下限。 */
export const SIDEBAR_MIN = 264
/** sidebar 拖拽上限。 */
export const SIDEBAR_MAX = 420
/** sidebar 默认宽（用户拖拽前）。 */
export const SIDEBAR_DEFAULT = 280
/** details 拖拽下限。 */
export const DETAILS_MIN = 300
/** details 拖拽上限。 */
export const DETAILS_MAX = 520
/** details 默认宽（用户拖拽前）。 */
export const DETAILS_DEFAULT = 360

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
