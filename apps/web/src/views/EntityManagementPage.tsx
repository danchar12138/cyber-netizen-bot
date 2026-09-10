import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Copy, Plus, Power, PowerOff, Users } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  copyManagedAgent,
  createManagedAgent,
  getAdminSession,
  getManagedAgents,
  getManagedUsers,
  updateManagedAgentStatus,
  updateManagedUserStatus,
} from '../api'
import { setSelectedAgentId } from '../agentSelection'
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
  const [newAgentName, setNewAgentName] = useState('')
  const [copySourceId, setCopySourceId] = useState('')
  const [copyAgentName, setCopyAgentName] = useState('')
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
      await Promise.all([
        invalidateAcrossTabs(queryClient, ['managed-entities', kind]),
        ...(isAgent
          ? [invalidateAcrossTabs(queryClient, ['managed-agents', 'selector'])]
          : []),
      ])
    },
  })
  const finishAgentCreation = async (agent: EntityRow | {
    id: string
    name: string
  }) => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['managed-entities', 'agents']),
      queryClient.invalidateQueries({ queryKey: ['managed-agents', 'selector'] }),
    ])
    setSelectedAgentId(agent.id)
  }
  const createAgent = useMutation({
    mutationFn: () => createManagedAgent(newAgentName.trim()),
    onSuccess: async (agent) => {
      setNewAgentName('')
      await finishAgentCreation(agent)
    },
  })
  const copyAgent = useMutation({
    mutationFn: () => copyManagedAgent(copySourceId, copyAgentName.trim()),
    onSuccess: async (agent) => {
      setCopyAgentName('')
      await finishAgentCreation(agent)
    },
  })

  useEffect(() => {
    if (!isAgent || copySourceId || !entities.data?.[0]) return
    setCopySourceId(entities.data[0].id)
  }, [copySourceId, entities.data, isAgent])

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
      {(updateStatus.error || createAgent.error || copyAgent.error) && (
        <div className="notice error">
          {(updateStatus.error ?? createAgent.error ?? copyAgent.error)?.message}
        </div>
      )}

      {isAgent && canWrite && (
        <section className="panel agent-create-panel" aria-label="创建或复制 Agent">
          <form onSubmit={(event) => {
            event.preventDefault()
            if (newAgentName.trim()) createAgent.mutate()
          }}>
            <div><strong>新建 Agent</strong><span>创建空白认知空间，后续独立配置人格与模型。</span></div>
            <input
              aria-label="新 Agent 名称"
              maxLength={120}
              placeholder="输入新 Agent 名称"
              value={newAgentName}
              onChange={(event) => setNewAgentName(event.target.value)}
            />
            <button className="primary-button" disabled={!newAgentName.trim() || createAgent.isPending}>
              <Plus size={14} /> 创建并切换
            </button>
          </form>
          <form onSubmit={(event) => {
            event.preventDefault()
            if (copySourceId && copyAgentName.trim()) copyAgent.mutate()
          }}>
            <div><strong>复制 Agent</strong><span>复制源 Agent 已发布的认知资源，并从版本 1 独立演进。</span></div>
            <select
              aria-label="源 Agent"
              value={copySourceId}
              onChange={(event) => setCopySourceId(event.target.value)}
            >
              {(entities.data ?? []).map((agent) => (
                <option key={agent.id} value={agent.id}>{agent.name}</option>
              ))}
            </select>
            <input
              aria-label="复制后的 Agent 名称"
              maxLength={120}
              placeholder="输入副本名称"
              value={copyAgentName}
              onChange={(event) => setCopyAgentName(event.target.value)}
            />
            <button className="secondary-button" disabled={!copySourceId || !copyAgentName.trim() || copyAgent.isPending}>
              <Copy size={14} /> 复制并切换
            </button>
          </form>
        </section>
      )}

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
