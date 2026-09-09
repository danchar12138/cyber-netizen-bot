import { Search } from 'lucide-react'
import type { ReactNode } from 'react'
import { useMemo, useState } from 'react'

export interface AdminTableColumn<T> {
  key: string
  label: string
  render: (row: T) => ReactNode
}

export function AdminDataTable<T extends object>({
  rows,
  columns,
  rowKey,
  searchableText,
  searchPlaceholder = '搜索当前列表',
  emptyMessage = '暂无数据',
}: {
  rows: T[]
  columns: Array<AdminTableColumn<T>>
  rowKey: (row: T) => string
  searchableText: (row: T) => string
  searchPlaceholder?: string
  emptyMessage?: string
}) {
  const [search, setSearch] = useState('')
  const visibleRows = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return query
      ? rows.filter((row) => searchableText(row).toLocaleLowerCase().includes(query))
      : rows
  }, [rows, search, searchableText])

  return (
    <div className="admin-table-wrap">
      <div className="admin-table-toolbar">
        <label className="search-box">
          <Search size={15} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={searchPlaceholder}
          />
        </label>
        <span>{visibleRows.length} 条结果</span>
      </div>
      <div className="admin-table-scroll">
        <table className="admin-table">
          <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={rowKey(row)}>
                {columns.map((column) => <td key={column.key}>{column.render(row)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {visibleRows.length === 0 && <div className="admin-table-empty">{emptyMessage}</div>}
      </div>
    </div>
  )
}
