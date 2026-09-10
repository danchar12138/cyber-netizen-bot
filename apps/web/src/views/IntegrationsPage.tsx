import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fingerprint, Link2, ListRestart, MessageSquareLock, Plus, ShieldCheck } from 'lucide-react'
import { useCallback, useMemo, useState, type FormEvent } from 'react'

import {
  createExternalConversationMapping,
  createExternalIdentityMapping,
  getAdminSession,
  getChannelInstances,
  getConversations,
  getExternalConversationMappings,
  getExternalIdentityMappings,
  getInboundEvents,
  getManagedUsers,
  replayInboundEvent,
  updateExternalConversationMappingStatus,
  updateExternalIdentityMappingStatus,
  type ExternalConversationKind,
  type ExternalConversationMapping,
  type ExternalIdentityMapping,
  type InboxEvent,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import { backgroundJobStatusLabels, channelPlatformLabels } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

const mappingStatusLabels = { enabled: '已启用', disabled: '已停用' } as const
const conversationKindLabels = { direct: '私聊', group: '群聊' } as const
const inboxStatusLabels = {
  pending: '待处理', processing: '处理中', completed: '已完成', dead_letter: '死信', canceled: '已取消',
} as const
const runStatusLabels = {
  queued: '排队中', running: '运行中', completed: '已完成', cancelled: '已取消', failed: '失败',
} as const

function shortDigest(value: string | null) {
  return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : '根会话'
}

export function IntegrationsPage() {
  const selectedAgentId = useSelectedAgentId()
  const queryClient = useQueryClient()
  const [identityChannelId, setIdentityChannelId] = useState('')
  const [externalSubjectId, setExternalSubjectId] = useState('')
  const [identityUserId, setIdentityUserId] = useState('')
  const [conversationChannelId, setConversationChannelId] = useState('')
  const [conversationUserId, setConversationUserId] = useState('')
  const [conversationId, setConversationId] = useState('')
  const [conversationKind, setConversationKind] = useState<ExternalConversationKind>('direct')
  const [externalConversationId, setExternalConversationId] = useState('')
  const [externalThreadId, setExternalThreadId] = useState('')

  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const channels = useQuery({ queryKey: ['channel-instances', selectedAgentId], queryFn: getChannelInstances })
  const users = useQuery({ queryKey: ['managed-users', 'integration'], queryFn: () => getManagedUsers('', 'active') })
  const conversations = useQuery({ queryKey: ['conversations', selectedAgentId, 'integration'], queryFn: () => getConversations('', 'active') })
  const identities = useQuery({ queryKey: ['external-identity-mappings', selectedAgentId], queryFn: getExternalIdentityMappings })
  const routes = useQuery({ queryKey: ['external-conversation-mappings', selectedAgentId], queryFn: getExternalConversationMappings })
  const inbox = useQuery({ queryKey: ['inbound-events', selectedAgentId], queryFn: getInboundEvents, refetchInterval: 15_000 })

  const canManage = session.data?.permissions.includes('integration:manage') ?? false
  const canReplay = session.data?.permissions.includes('inbox:replay') ?? false
  const refreshMappings = async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['external-identity-mappings']),
      invalidateAcrossTabs(queryClient, ['external-conversation-mappings']),
      invalidateAcrossTabs(queryClient, ['inbound-events']),
    ])
  }
  const createIdentity = useMutation({
    mutationFn: () => createExternalIdentityMapping({ channel_id: identityChannelId, external_subject_id: externalSubjectId, user_id: identityUserId }),
    onSuccess: async () => { setExternalSubjectId(''); await refreshMappings() },
  })
  const createRoute = useMutation({
    mutationFn: () => createExternalConversationMapping({
      channel_id: conversationChannelId, user_id: conversationUserId, kind: conversationKind,
      external_conversation_id: externalConversationId, external_thread_id: externalThreadId.trim() || null,
      conversation_id: conversationId,
    }),
    onSuccess: async () => { setExternalConversationId(''); setExternalThreadId(''); await refreshMappings() },
  })
  const identityStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'enabled' | 'disabled' }) => updateExternalIdentityMappingStatus(id, status),
    onSuccess: refreshMappings,
  })
  const routeStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'enabled' | 'disabled' }) => updateExternalConversationMappingStatus(id, status),
    onSuccess: refreshMappings,
  })
  const replay = useMutation({
    mutationFn: (id: string) => replayInboundEvent(id, '管理员确认映射与处理条件已恢复'),
    onSuccess: refreshMappings,
  })

  const submitIdentity = (event: FormEvent) => {
    event.preventDefault()
    if (identityChannelId && externalSubjectId.trim() && identityUserId) createIdentity.mutate()
  }
  const submitRoute = (event: FormEvent) => {
    event.preventDefault()
    if (conversationChannelId && conversationUserId && conversationId && externalConversationId.trim()) createRoute.mutate()
  }
  const toggleIdentity = useCallback((row: ExternalIdentityMapping) => {
    const next = row.status === 'enabled' ? 'disabled' : 'enabled'
    if (window.confirm(`确认${next === 'enabled' ? '启用' : '停用'}该身份映射？`)) identityStatus.mutate({ id: row.id, status: next })
  }, [identityStatus])
  const toggleRoute = useCallback((row: ExternalConversationMapping) => {
    const next = row.status === 'enabled' ? 'disabled' : 'enabled'
    if (window.confirm(`确认${next === 'enabled' ? '启用' : '停用'}该会话路由？`)) routeStatus.mutate({ id: row.id, status: next })
  }, [routeStatus])

  const identityColumns = useMemo<Array<AdminTableColumn<ExternalIdentityMapping>>>(() => [
    { key: 'subject', label: '平台主体', render: (row) => <div className="table-primary"><strong>{row.external_subject_id}</strong><code>{channelPlatformLabels[row.platform]} · {row.id}</code></div> },
    { key: 'user', label: '本地用户', render: (row) => <code>{row.user_id}</code> },
    { key: 'status', label: '状态', render: (row) => <span className={`entity-status ${row.status === 'enabled' ? 'active' : 'disabled'}`}>{mappingStatusLabels[row.status]}</span> },
    { key: 'actions', label: '操作', render: (row) => <button disabled={!canManage} onClick={() => toggleIdentity(row)}>{row.status === 'enabled' ? '停用' : '启用'}</button> },
  ], [canManage, toggleIdentity])
  const routeColumns = useMemo<Array<AdminTableColumn<ExternalConversationMapping>>>(() => [
    { key: 'external', label: '外部路由', render: (row) => <div className="table-primary"><strong>{row.external_conversation_id}</strong><code>{row.external_thread_id ? `线程 ${row.external_thread_id}` : '根会话'} · {conversationKindLabels[row.kind]}</code></div> },
    { key: 'local', label: '内部会话', render: (row) => <code>{row.conversation_id}</code> },
    { key: 'status', label: '状态', render: (row) => <span className={`entity-status ${row.status === 'enabled' ? 'active' : 'disabled'}`}>{mappingStatusLabels[row.status]}</span> },
    { key: 'actions', label: '操作', render: (row) => <button disabled={!canManage} onClick={() => toggleRoute(row)}>{row.status === 'enabled' ? '停用' : '启用'}</button> },
  ], [canManage, toggleRoute])
  const inboxColumns = useMemo<Array<AdminTableColumn<InboxEvent>>>(() => [
    { key: 'event', label: '入站事件', render: (row) => <div className="table-primary"><strong>{row.event_type}</strong><code>Envelope v{row.schema_version} · {channelPlatformLabels[row.platform]}</code></div> },
    { key: 'digests', label: '安全标识摘要', render: (row) => <div className="digest-stack"><code title={row.external_subject_digest}>主体 {shortDigest(row.external_subject_digest)}</code><code title={row.external_conversation_digest}>会话 {shortDigest(row.external_conversation_digest)}</code><code title={row.external_message_digest}>消息 {shortDigest(row.external_message_digest)}</code></div> },
    { key: 'content', label: '内容元数据', render: (row) => `${row.content_block_count} 块 · ${row.content_kinds.join('、') || '无'}` },
    { key: 'status', label: '状态', render: (row) => <div className="channel-status-stack"><span className={`entity-status task-${row.status}`}>{inboxStatusLabels[row.status]}</span><small>{row.job_status ? backgroundJobStatusLabels[row.job_status] : '任务不可用'}</small></div> },
    { key: 'agent-run', label: 'Agent Run', render: (row) => row.run_id ? <div className="table-primary"><strong>{row.run_status ? runStatusLabels[row.run_status] : '状态待同步'}</strong><code title={row.run_id}>{row.run_id}</code><small>{row.idempotent_replay ? '幂等重放' : '首次处理'}</small></div> : <span>尚未创建</span> },
    { key: 'received', label: '接收时间', render: (row) => new Date(row.received_at).toLocaleString('zh-CN') },
    { key: 'actions', label: '操作', render: (row) => <button disabled={!canReplay || !['dead_letter', 'canceled'].includes(row.status)} onClick={() => window.confirm('确认从已净化 Envelope 安全重放？') && replay.mutate(row.id)}><ListRestart size={12} />重放</button> },
  ], [canReplay, replay])

  const error = createIdentity.error ?? createRoute.error ?? identityStatus.error ?? routeStatus.error ?? replay.error
  const loadingError = channels.isError || users.isError || conversations.isError || identities.isError || routes.isError || inbox.isError
  const enabledChannels = channels.data?.items.filter((item) => item.status === 'enabled') ?? []
  const activeUsers = users.data?.items ?? []
  const activeConversations = conversations.data?.items ?? []

  return <div className="page">
    <section className="page-heading compact">
      <div><p className="eyebrow">真实 IM 接入前的稳定路由边界</p><h1>外部身份与 Inbox</h1><p>用不可变平台 ID 映射本地身份与会话；入站事件完成验签、净化和幂等落库后，由 Worker 接入真实 Conversation 与 Agent Run。</p></div>
      <span className="phase-tag">Envelope v1</span>
    </section>
    {loadingError && <div className="notice error">外部接入数据读取失败，请检查 API 与迁移状态。</div>}
    {error && <div className="notice error">{error.message}</div>}
    <div className="notice info"><ShieldCheck size={17} /><div><strong>诊断页不展示消息正文、凭证、对象键或远端响应</strong><span>列表仅保留路由 UUID、内容块种类与 SHA-256 标识摘要；重放只复用已净化 Envelope。</span></div></div>

    <section className="integration-form-grid">
      <article className="panel integration-form-card">
        <div className="panel-heading"><div><span>主体解析</span><h2><Fingerprint size={17} />绑定外部身份</h2></div></div>
        <form className="integration-form" onSubmit={submitIdentity}>
          <label><span>渠道实例</span><select value={identityChannelId} onChange={(event) => setIdentityChannelId(event.target.value)} required><option value="">选择渠道</option>{enabledChannels.map((item) => <option key={item.id} value={item.id}>{item.name} · {channelPlatformLabels[item.platform]}</option>)}</select></label>
          <label><span>外部主体 ID</span><input value={externalSubjectId} onChange={(event) => setExternalSubjectId(event.target.value)} maxLength={255} placeholder="只使用平台稳定 ID" required /></label>
          <label><span>本地用户</span><select value={identityUserId} onChange={(event) => setIdentityUserId(event.target.value)} required><option value="">选择用户</option>{activeUsers.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
          <button className="primary-button" disabled={!canManage || createIdentity.isPending}><Plus size={14} />创建身份映射</button>
        </form>
      </article>
      <article className="panel integration-form-card">
        <div className="panel-heading"><div><span>线程路由</span><h2><Link2 size={17} />绑定会话与线程</h2></div></div>
        <form className="integration-form" onSubmit={submitRoute}>
          <label><span>渠道实例</span><select value={conversationChannelId} onChange={(event) => setConversationChannelId(event.target.value)} required><option value="">选择渠道</option>{enabledChannels.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label><span>本地用户</span><select value={conversationUserId} onChange={(event) => setConversationUserId(event.target.value)} required><option value="">选择用户</option>{activeUsers.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
          <label><span>内部会话</span><select aria-label="内部会话" value={conversationId} onChange={(event) => setConversationId(event.target.value)} required><option value="">选择会话</option>{activeConversations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
          <label><span>会话类型</span><select value={conversationKind} onChange={(event) => setConversationKind(event.target.value as ExternalConversationKind)}><option value="direct">私聊</option><option value="group">群聊</option></select></label>
          <label><span>外部会话 ID</span><input value={externalConversationId} onChange={(event) => setExternalConversationId(event.target.value)} maxLength={255} required /></label>
          <label><span>外部线程 ID（可选）</span><input value={externalThreadId} onChange={(event) => setExternalThreadId(event.target.value)} maxLength={255} /></label>
          <button className="primary-button" disabled={!canManage || createRoute.isPending}><Plus size={14} />创建路由映射</button>
        </form>
      </article>
    </section>

    <section className="panel table-panel"><div className="panel-heading"><div><span>平台主体</span><h2>外部身份映射</h2></div><small>{identities.data?.items.length ?? 0} 条</small></div><AdminDataTable rows={identities.data?.items ?? []} columns={identityColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.external_subject_id} ${row.user_id} ${row.platform}`} searchPlaceholder="搜索主体 ID、用户或平台" emptyMessage={identities.isLoading ? '正在读取身份映射…' : '尚无身份映射'} /></section>
    <section className="panel table-panel"><div className="panel-heading"><div><span>稳定线程</span><h2>会话路由映射</h2></div><small>{routes.data?.items.length ?? 0} 条</small></div><AdminDataTable rows={routes.data?.items ?? []} columns={routeColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.external_conversation_id} ${row.external_thread_id ?? ''} ${row.conversation_id}`} searchPlaceholder="搜索外部会话、线程或内部会话" emptyMessage={routes.isLoading ? '正在读取会话映射…' : '尚无会话映射'} /></section>
    <section className="panel table-panel"><div className="panel-heading"><div><span>每 15 秒刷新</span><h2><MessageSquareLock size={17} />Inbox 安全诊断</h2></div><small>{inbox.data?.items.length ?? 0} 条</small></div><AdminDataTable rows={inbox.data?.items ?? []} columns={inboxColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.id} ${row.event_type} ${row.status} ${row.external_event_digest}`} searchPlaceholder="搜索 Inbox ID、类型、状态或摘要" emptyMessage={inbox.isLoading ? '正在读取 Inbox…' : '尚无入站事件'} /></section>
  </div>
}
