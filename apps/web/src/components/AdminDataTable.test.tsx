import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AdminDataTable } from './AdminDataTable'

describe('AdminDataTable', () => {
  it('可以按统一搜索文本过滤管理列表', () => {
    const rows = [{ id: 'admin', name: '管理员' }, { id: 'viewer', name: '只读访客' }]
    render(
      <AdminDataTable
        rows={rows}
        columns={[{ key: 'name', label: '角色', render: (row) => row.name }]}
        rowKey={(row) => row.id}
        searchableText={(row) => `${row.id} ${row.name}`}
      />,
    )

    fireEvent.change(screen.getByPlaceholderText('搜索当前列表'), {
      target: { value: 'viewer' },
    })

    expect(screen.getByText('只读访客')).toBeInTheDocument()
    expect(screen.queryByText('管理员')).not.toBeInTheDocument()
    expect(screen.getByText('1 条结果')).toBeInTheDocument()
  })
})
