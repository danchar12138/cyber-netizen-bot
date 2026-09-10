import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Power, PowerOff, Users } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import {
  getAdminSession,
  getManagedAgents,
  getManagedUsers,
  updateManagedAgentStatus,
  updateManagedUserStatus,
} from '../api'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import { adminRoleLabels } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

interface EntityRow {
  id: string
  tenantId: string
  name: string
  status: 'active' | 'disabled'
  createdAt: string
}

export function EntityManagementPage({ kind }: { kind: 'agents' | 'users' }) {
  const isAgent = kind === 'agents'
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canWrite = session.data?.permissions.includes(isAgent ? 'agent:write' : 'user:write') ?? false
  const entities = useQuery({
    queryKey: ['managed-entities', kind],
    queryFn: async (): Promise<EntityRow[]> => {
      if (isAgent) {
        const response = await getManagedAgents()
        return response.items.map((item) => ({
          id: item.id,
          tenantId: item.tenant_id,
          name: item.name,
          status: item.status,
          createdAt: item.created_at,
        }))
      }
      const response = await getManagedUsers()
      return response.items.map((item) => ({
        id: item.id,
        tenantId: item.tenant_id,
        name: item.display_name,
        status: item.status,
        createdAt: item.created_at,
      }))
    },
  })
  const updateStatus = useMutation({
    mutationFn: async (status: 'active' | 'disabled') => {
      if (isAgent) await updateManagedAgentStatus([...selected], status)
      else await updateManagedUserStatus([...selected], status)
    },
    onSuccess: async () => {
      setSelected(new Set())
      await invalidateAcrossTabs(queryClient, ['managed-entities', kind])
    },
  })

  const columns = useMemo<Array<AdminTableColumn<EntityRow>>>(() => [
    {
      key: 'name',
      label: isAgent ? 'Agent' : '用户',
      render: (row) => <div className="table-primary"><strong>{row.name}</strong><code>{row.id}</code></div>,
    },
    {
      key: 'status',
      label: '状态',
      render: (row) => <span className={`entity-status ${row.status}`}>{row.status === 'active' ? '已启用' : '已停用'}</span>,
    },
    { key: 'tenant', label: '租户 ID', render: (row) => <code>{row.tenantId}</code> },
    { key: 'created', label: '创建时间', render: (row) => new Date(row.createdAt).toLocaleString('zh-CN') },
  ], [isAgent])
  const searchableText = useCallback((row: EntityRow) => `${row.name} ${row.id} ${row.status}`, [])

  const runBulk = (status: 'active' | 'disabled') => {
    const action = status === 'active' ? '启用' : '停用'
    if (window.confirm(`确认${action}已选择的 ${selected.size} 个${isAgent ? ' Agent' : '用户'}？此操作会写入审计日志。`)) {
      updateStatus.mutate(status)
    }
  }
  const Icon = isAgent ? Bot : Users

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">资源管理</p>
          <h1>{isAgent ? 'Agent 管理' : '用户与身份'}</h1>
          <p>{isAgent ? '查看当前租户 Agent，并安全执行批量启停；人格版本通过独立入口治理。' : '查看当前租户用户，并通过有确认和审计的批量操作管理状态。'}</p>
        </div>
        <div className="heading-actions">
          <button className="secondary-button" disabled={!canWrite || selected.size === 0 || updateStatus.isPending} onClick={() => runBulk('active')}><Power size={14} /> 批量启用</button>
          <button className="danger-button" disabled={!canWrite || selected.size === 0 || updateStatus.isPending} onClick={() => runBulk('disabled')}><PowerOff size={14} /> 批量停用</button>
        </div>
      </section>

      <div className="notice info">
        <Icon size={17} />
        <div>
          <strong>{isAgent ? `已选择 ${selected.size} 项` : `当前身份：${session.data?.display_name ?? '读取中'} · ${session.data ? adminRoleLabels[session.data.role] : '—'}`}</strong>
          <span>{isAgent ? '状态变更由服务端再次校验租户边界、权限和明确确认字段。' : `租户 ${session.data?.tenant_id ?? '读取中'}；用户状态变更同样经过租户隔离、权限和确认校验。`}</span>
        </div>
      </div>
      {updateStatus.error && <div className="notice error">{updateStatus.error.message}</div>}

      <section className="panel table-panel">
        <AdminDataTable
          rows={entities.data ?? []}
          columns={columns}
          rowKey={(row) => row.id}
          searchableText={searchableText}
          searchPlaceholder={`搜索${isAgent ? ' Agent' : '用户'}名称或 ID`}
          emptyMessage={entities.isLoading ? '正在读取资源…' : '暂无匹配资源'}
          selectedKeys={selected}
          onSelectionChange={setSelected}
        />
      </section>
    </div>
  )
}
