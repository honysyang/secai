/** DataTable：泛型表格（排序 + 斑马纹 + 行点击）。 */

import { useState, useMemo } from 'react'
import type { ReactNode } from 'react'
import clsx from 'clsx'
import css from './DataTable.module.css'

export interface ColumnDef<T> {
  key: keyof T & string
  header: string
  width?: number
  render?: (row: T) => ReactNode
  sortable?: boolean
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[]
  rows: T[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  className?: string
}

type SortDir = 'asc' | 'desc'

export function DataTable<T>({ columns, rows, rowKey, onRowClick, className }: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  const sorted = useMemo(() => {
    if (sortKey === null) return rows
    const col = columns.find((c) => c.key === sortKey)
    if (!col) return rows
    const key = col.key as keyof T
    return [...rows].sort((a, b) => {
      const va = a[key]
      const vb = b[key]
      if (va === vb) return 0
      const cmp = va < vb ? -1 : 1
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [rows, sortKey, sortDir, columns])

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  return (
    <div className={clsx(css.wrap, className)}>
      <table className={css.table}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                style={col.width !== undefined ? { width: col.width } : undefined}
                className={clsx(col.sortable && css.sortable)}
                onClick={col.sortable ? () => toggleSort(col.key) : undefined}
              >
                <span className={css.thInner}>
                  {col.header}
                  {col.sortable && sortKey === col.key && (
                    <span className={css.sortIcon}>{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={rowKey(row)}
              className={clsx(onRowClick && css.clickable, i % 2 === 1 && css.zebra)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.key}>
                  {col.render ? col.render(row) : String(row[col.key] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
