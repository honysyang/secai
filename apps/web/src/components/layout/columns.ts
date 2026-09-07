/**
 * 两栏让步链求解器（纯函数，复刻 dsh ui-layout columns.ts 语义，去掉右栏）。
 *
 * dsh 两栏合同：sidebar min264/max420/default280（收起成 56px rail），
 * 中心栏吸收剩余全部宽度（无下限约束，窄视口自然压缩）。
 * sidebar 永不让步（渲染宽 = 拖拽偏好），输入是布局 store 的普通宽度
 * 偏好（px，0 = 收起），输出是渲染决议，因此无迟滞：收窄-回宽自动可逆。
 */

/** 本次渲染的两栏决议宽。 */
export interface Columns {
  sidebar: number
  center: number
}

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
/** 工作台路由（边栏含会话管理）时的侧栏拖拽下限。 */
export const WIDE_MIN = 264
/** 工作台路由（边栏含会话管理）时的侧栏拖拽上限。 */
export const WIDE_MAX = 420

/** 把面板宽夹紧到契约范围（偏好跨 store 边界回传时可能越界，须重夹）。 */
export function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(px)))
}

/** 求解一个视口下的两栏宽；纯函数，(viewport, 偏好, wide) → 决议。
 * 偏好 0 = 收起成 rail（渲染宽 = SIDEBAR_COLLAPSED 56px，保留展开钮）。
 * wide = true（工作台路由）时用 WIDE_MIN/WIDE_MAX 区间，容纳边栏内嵌的会话管理。 */
export function computeColumns(viewport: number, sidebar: number, wide = false): Columns {
  if (sidebar === 0) return { sidebar: SIDEBAR_COLLAPSED, center: Math.max(0, viewport - SIDEBAR_COLLAPSED) }
  const min = wide ? WIDE_MIN : SIDEBAR_MIN
  const max = wide ? WIDE_MAX : SIDEBAR_MAX
  const s = clampWidth(sidebar, min, max)
  return { sidebar: s, center: Math.max(0, viewport - s) }
}
