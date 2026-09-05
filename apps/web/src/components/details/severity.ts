/** 详情区共享：severity 标签中文名（critical/high/medium/low/info）。 */

import type { Severity } from '../../runtime/projections.ts'

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
  info: '信息',
}
