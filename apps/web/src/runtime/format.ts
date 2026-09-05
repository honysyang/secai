/** 展示用时间格式化（中文化，避开时区歧义）。 */

/** "HH:MM"（24h）；非法/空输入回 ""。 */
export function formatClock(iso: string | null | undefined): string {
  if (iso === undefined || iso === null || iso === '') return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** "MM-DD HH:MM"；非法/空输入回 ""。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (iso === undefined || iso === null || iso === '') return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const datePart = date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  const timePart = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  return `${datePart} ${timePart}`
}
