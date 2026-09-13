import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Bot, Copy, Pencil, Plus, Power, PowerOff, Trash2, Users } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  archiveManagedAgent,
  copyManagedAgent,
  createManagedAgent,
  getAdminSession,
  getManagedAgentImpact,
  getManagedAgents,
  getManagedUsers,
  renameManagedAgent,
  softDeleteManagedAgent,
  updateManagedAgentStatus,
  updateManagedUserStatus,
} from '../api'
import { setSelectedAgentId } from '../agentSelection'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import { UserIdentityPanel } from '../components/UserIdentityPanel'
import { adminRoleLabels } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

type EntityLifecycleStatus = 'active' | 'disabled' | 'archived' | 'deleted'

interface EntityRow {
  id: string
  tenantId: string
  name: string
  status: EntityLifecycleStatus
  createdAt: string
  archivedAt: string | null
  deletedAt: string | null
  purgeAfter: string | null
}

const statusLabels: Record<EntityLifecycleStatus, string> = {
  active: '已启用',
  disabled: '已停用',
  archived: '已归档',
  deleted: '等待清理',
}

const impactLabels = {
  conversations: '会话',
  agent_runs: '智能体运行',
  cognition_resource_versions: '认知版本',
  memories: '记忆',
  relationships: '关系',
  evaluation_suites: '评测集',
  evaluation_runs: '评测运行',
  channel_instances: '渠道',
  scheduled_actions: '定时行为',
} as const

export function EntityManagementPage({ kind }: { kind: 'agents' | 'users' }) {
  const isAgent = kind === 'agents'
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [newAgentName, setNewAgentName] = useState('')
  const [copySourceId, setCopySourceId] = useState('')
  const [copyAgentName, setCopyAgentName] = useState('')
  const [renameAgentName, setRenameAgentName] = useState('')
  const [lifecycleConfirmation, setLifecycleConfirmation] = useState('')
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canWrite = session.data?.permissions.includes(isAgent ? 'agent:write' : 'user:write') ?? false
  const canManageUserRole = session.data?.permissions.includes('user:role_write') ?? false
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
          archivedAt: item.archived_at,
          deletedAt: item.deleted_at,
          purgeAfter: item.purge_after,
        }))
      }
      const response = await getManagedUsers()
      return response.items.map((item) => ({
        id: item.id,
        tenantId: item.tenant_id,
        name: item.display_name,
        status: item.status,
        createdAt: item.created_at,
        archivedAt: null,
        deletedAt: null,
        purgeAfter: null,
      }))
    },
  })
  const selectedAgentId = isAgent && selected.size === 1 ? ([...selected][0] ?? null) : null
  const selectedUserId = !isAgent && selected.size === 1 ? ([...selected][0] ?? null) : null
  const selectedAgent = useMemo(
    () => entities.data?.find((item) => item.id === selectedAgentId) ?? null,
    [entities.data, selectedAgentId],
  )
  const impact = useQuery({
    queryKey: ['managed-agent-impact', selectedAgentId],
    queryFn: () => getManagedAgentImpact(selectedAgentId ?? ''),
    enabled: selectedAgentId !== null,
  })

  const refreshAgentManagement = async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['managed-entities', 'agents']),
      invalidateAcrossTabs(queryClient, ['managed-agents', 'selector']),
      queryClient.invalidateQueries({ queryKey: ['managed-agent-impact', selectedAgentId] }),
    ])
  }
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
  const finishAgentCreation = async (agent: EntityRow | { id: string; name: string }) => {
    await refreshAgentManagement()
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
  const renameAgent = useMutation({
    mutationFn: async () => {
      if (selectedAgentId === null) throw new Error('请先选择一个智能体')
      return renameManagedAgent(selectedAgentId, renameAgentName.trim())
    },
    onSuccess: refreshAgentManagement,
  })
  const archiveAgent = useMutation({
    mutationFn: async () => {
      if (selectedAgentId === null) throw new Error('请先选择一个智能体')
      return archiveManagedAgent(selectedAgentId, lifecycleConfirmation)
    },
    onSuccess: async () => {
      setLifecycleConfirmation('')
      await refreshAgentManagement()
    },
  })
  const deleteAgent = useMutation({
    mutationFn: async () => {
      if (selectedAgentId === null) throw new Error('请先选择一个智能体')
      return softDeleteManagedAgent(selectedAgentId, lifecycleConfirmation)
    },
    onSuccess: async () => {
      setLifecycleConfirmation('')
      await refreshAgentManagement()
    },
  })

  useEffect(() => {
    if (!isAgent || copySourceId || !entities.data) return
    const firstActiveAgent = entities.data.find((item) => item.status === 'active')
    if (firstActiveAgent) setCopySourceId(firstActiveAgent.id)
  }, [copySourceId, entities.data, isAgent])

  useEffect(() => {
    setRenameAgentName(selectedAgent?.name ?? '')
    setLifecycleConfirmation('')
  }, [selectedAgent])

  const columns = useMemo<Array<AdminTableColumn<EntityRow>>>(() => [
    {
      key: 'name',
      label: isAgent ? '智能体' : '用户',
      render: (row) => <div className="table-primary"><strong>{row.name}</strong><code>{row.id}</code></div>,
    },
    {
      key: 'status',
      label: '状态',
      render: (row) => <span className={`entity-status ${row.status}`}>{statusLabels[row.status]}</span>,
    },
    { key: 'tenant', label: '租户 ID', render: (row) => <code>{row.tenantId}</code> },
    {
      key: 'lifecycle',
      label: isAgent ? '生命周期时间' : '创建时间',
      render: (row) => {
        const timestamp = row.purgeAfter ?? row.deletedAt ?? row.archivedAt ?? row.createdAt
        const prefix = row.purgeAfter ? '最早清理 ' : row.deletedAt ? '删除 ' : row.archivedAt ? '归档 ' : ''
        return `${prefix}${new Date(timestamp).toLocaleString('zh-CN')}`
      },
    },
  ], [isAgent])
  const searchableText = useCallback((row: EntityRow) => `${row.name} ${row.id} ${row.status}`, [])
  const selectedRows = (entities.data ?? []).filter((item) => selected.has(item.id))
  const bulkStatusAllowed = selectedRows.every(
    (item) => item.status === 'active' || item.status === 'disabled',
  )
  const pendingError = updateStatus.error
    ?? createAgent.error
    ?? copyAgent.error
    ?? renameAgent.error
    ?? archiveAgent.error
    ?? deleteAgent.error
    ?? impact.error

  const runBulk = (status: 'active' | 'disabled') => {
    const action = status === 'active' ? '启用' : '停用'
    if (window.confirm(`确认${action}已选择的 ${selected.size} 个${isAgent ? '智能体' : '用户'}？此操作会写入审计日志。`)) {
      updateStatus.mutate(status)
    }
  }
  const Icon = isAgent ? Bot : Users

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">资源管理</p>
          <h1>{isAgent ? '智能体管理' : '用户与身份'}</h1>
          <p>{isAgent ? '管理智能体的创建、复制、命名、启停、归档与软删除保留期；人格版本通过独立入口治理。' : '集中管理当前租户用户的状态、请求预算、临时停用、可信角色、身份绑定与管理会话。'}</p>
        </div>
        <div className="heading-actions">
          <button className="secondary-button" disabled={!canWrite || selected.size === 0 || !bulkStatusAllowed || updateStatus.isPending} onClick={() => runBulk('active')}><Power size={14} /> 批量启用</button>
          <button className="danger-button" disabled={!canWrite || selected.size === 0 || !bulkStatusAllowed || updateStatus.isPending} onClick={() => runBulk('disabled')}><PowerOff size={14} /> 批量停用</button>
        </div>
      </section>

      <div className="notice info">
        <Icon size={17} />
        <div>
          <strong>{isAgent ? `已选择 ${selected.size} 项` : `当前身份：${session.data?.display_name ?? '读取中'} · ${session.data ? adminRoleLabels[session.data.role] : '—'}`}</strong>
          <span>{isAgent ? '选择一个智能体可自动加载依赖影响预览；归档与删除均要求逐字确认。' : `租户 ${session.data?.tenant_id ?? '读取中'}；用户状态变更同样经过租户隔离、权限和确认校验。`}</span>
        </div>
      </div>
      {pendingError && <div className="notice error" role="alert">{pendingError.message}</div>}

      {isAgent && canWrite && (
        <section className="panel agent-create-panel" aria-label="创建或复制智能体">
          <form onSubmit={(event) => {
            event.preventDefault()
            if (newAgentName.trim()) createAgent.mutate()
          }}>
            <div><strong>新建智能体</strong><span>创建空白认知空间，后续独立配置人格与模型。</span></div>
            <input
              aria-label="新智能体名称"
              maxLength={120}
              placeholder="输入新智能体名称"
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
            <div><strong>复制智能体</strong><span>复制源智能体已发布的认知资源，并从版本 1 独立演进。</span></div>
            <select aria-label="源智能体" value={copySourceId} onChange={(event) => setCopySourceId(event.target.value)}>
              {(entities.data ?? []).filter((agent) => agent.status === 'active').map((agent) => (
                <option key={agent.id} value={agent.id}>{agent.name}</option>
              ))}
            </select>
            <input
              aria-label="复制后的智能体名称"
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

      {isAgent && selectedAgent && (
        <section className="panel agent-lifecycle-panel" aria-label="智能体生命周期管理">
          <div className="panel-heading">
            <div><p className="eyebrow">安全生命周期</p><h2>{selectedAgent.name}</h2></div>
            <span className={`entity-status ${selectedAgent.status}`}>{statusLabels[selectedAgent.status]}</span>
          </div>
          <form className="agent-rename-form" onSubmit={(event) => {
            event.preventDefault()
            if (renameAgentName.trim()) renameAgent.mutate()
          }}>
            <label htmlFor="agent-rename">智能体名称</label>
            <input id="agent-rename" maxLength={120} value={renameAgentName} onChange={(event) => setRenameAgentName(event.target.value)} />
            <button className="secondary-button" disabled={!canWrite || selectedAgent.status === 'deleted' || !renameAgentName.trim() || renameAgent.isPending}><Pencil size={14} /> 保存名称</button>
          </form>

          {impact.isLoading && <p>正在统计关联资源…</p>}
          {impact.data && (
            <>
              <div className="agent-impact-grid" aria-label="关联资源影响统计">
                {Object.entries(impactLabels).map(([key, label]) => (
                  <div key={key}><span>{label}</span><strong>{impact.data.counts[key as keyof typeof impactLabels]}</strong></div>
                ))}
                <div className="total"><span>顶层记录合计</span><strong>{impact.data.counts.total}</strong></div>
              </div>
              <div className="notice warning">
                <Archive size={17} />
                <div>
                  <strong>其他已启用智能体：{impact.data.active_replacement_count} 个</strong>
                  <span>归档会立即阻止新运行、停用渠道并取消待执行主动行为；软删除后保留 {impact.data.deleted_agent_retention_days} 天，当前操作不会物理删除数据。</span>
                </div>
              </div>
              {impact.data.blockers.length > 0 && (
                <ul className="agent-lifecycle-blockers">
                  {impact.data.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                </ul>
              )}
              {selectedAgent.purgeAfter && (
                <p className="agent-purge-time">最早物理清理时间：{new Date(selectedAgent.purgeAfter).toLocaleString('zh-CN')}</p>
              )}
              {(impact.data.can_archive || impact.data.can_delete) && canWrite && (
                <div className="agent-confirmation-box">
                  <label htmlFor="agent-lifecycle-confirmation">逐字输入确认短语</label>
                  <code>{impact.data.can_delete ? impact.data.delete_confirmation : impact.data.archive_confirmation}</code>
                  <input id="agent-lifecycle-confirmation" aria-label="智能体生命周期确认短语" value={lifecycleConfirmation} onChange={(event) => setLifecycleConfirmation(event.target.value)} />
                  <div className="heading-actions">
                    {impact.data.can_archive && (
                      <button className="danger-button" disabled={lifecycleConfirmation !== impact.data.archive_confirmation || archiveAgent.isPending} onClick={() => archiveAgent.mutate()}><Archive size={14} /> 确认归档</button>
                    )}
                    {impact.data.can_delete && (
                      <button className="danger-button" disabled={lifecycleConfirmation !== impact.data.delete_confirmation || deleteAgent.isPending} onClick={() => deleteAgent.mutate()}><Trash2 size={14} /> 进入软删除保留期</button>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      )}

      {selectedUserId && (
        <UserIdentityPanel
          userId={selectedUserId}
          currentUserId={session.data?.user_id}
          canWrite={canWrite}
          canManageRole={canManageUserRole}
        />
      )}

      <section className="panel table-panel">
        <AdminDataTable
          rows={entities.data ?? []}
          columns={columns}
          rowKey={(row) => row.id}
          searchableText={searchableText}
          searchPlaceholder={`搜索${isAgent ? '智能体' : '用户'}名称或 ID`}
          emptyMessage={entities.isLoading ? '正在读取资源…' : '暂无匹配资源'}
          selectedKeys={selected}
          onSelectionChange={setSelected}
        />
      </section>
    </div>
  )
}
