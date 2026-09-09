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
  selectedKeys,
  onSelectionChange,
}: {
  rows: T[]
  columns: Array<AdminTableColumn<T>>
  rowKey: (row: T) => string
  searchableText: (row: T) => string
  searchPlaceholder?: string
  emptyMessage?: string
  selectedKeys?: ReadonlySet<string>
  onSelectionChange?: (keys: Set<string>) => void
}) {
  const [search, setSearch] = useState('')
  const visibleRows = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return query
      ? rows.filter((row) => searchableText(row).toLocaleLowerCase().includes(query))
      : rows
  }, [rows, search, searchableText])
  const selectable = selectedKeys !== undefined && onSelectionChange !== undefined
  const currentSelected = selectedKeys ?? new Set<string>()
  const allVisibleSelected = selectable
    && visibleRows.length > 0
    && visibleRows.every((row) => currentSelected.has(rowKey(row)))

  const toggleAll = () => {
    if (!selectedKeys || !onSelectionChange) return
    const next = new Set(selectedKeys)
    for (const row of visibleRows) {
      const key = rowKey(row)
      if (allVisibleSelected) next.delete(key)
      else next.add(key)
    }
    onSelectionChange(next)
  }

  const toggleRow = (key: string) => {
    if (!selectedKeys || !onSelectionChange) return
    const next = new Set(selectedKeys)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    onSelectionChange(next)
  }

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
          <thead><tr>
            {selectable && <th className="selection-cell"><input type="checkbox" aria-label="选择当前全部结果" checked={allVisibleSelected} onChange={toggleAll} /></th>}
            {columns.map((column) => <th key={column.key}>{column.label}</th>)}
          </tr></thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={rowKey(row)}>
                {selectable && (
                  <td className="selection-cell">
                    <input type="checkbox" aria-label={`选择 ${searchableText(row)}`} checked={currentSelected.has(rowKey(row))} onChange={() => toggleRow(rowKey(row))} />
                  </td>
                )}
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
