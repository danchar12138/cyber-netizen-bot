import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Clock3, GitBranch, ListRestart, ListTodo, Plus, ServerCog, ShieldAlert, XCircle } from 'lucide-react'
import { useCallback, useMemo, useState, type FormEvent } from 'react'

import {
  cancelBackgroundJob,
  cancelScheduledAction,
  createScheduledAction,
  getAdminSession,
  getBackgroundJobs,
  getBackgroundJobReplayChain,
  getChannelInstances,
  getDevelopmentIdentity,
  getScheduledActions,
  getTaskDashboard,
  replayBackgroundJob,
  type BackgroundJob,
  type BackgroundJobReplayChainItem,
  type BackgroundJobStatus,
  type ScheduledAction,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import {
  backgroundJobKindLabels,
  backgroundJobStatusLabels,
  formatMetadataEntries,
  scheduledActionKindLabels,
  scheduledActionStatusLabels,
} from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

function localInputValue(date: Date) {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

export function TaskStatusPage() {
  const queryClient = useQueryClient()
  const selectedAgentId = useSelectedAgentId()
  const [statusFilter, setStatusFilter] = useState<BackgroundJobStatus | ''>('')
  const [reason, setReason] = useState('在合适时间自然跟进上次交流')
  const [channelId, setChannelId] = useState('')
  const [recipientId, setRecipientId] = useState('')
  const [message, setMessage] = useState('想起你之前提到的事情，最近进展还顺利吗？')
  const [scheduledFor, setScheduledFor] = useState(() => localInputValue(new Date(Date.now() + 60_000)))
  const [replayChainJobId, setReplayChainJobId] = useState<string | null>(null)
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const identity = useQuery({ queryKey: ['development-identity', selectedAgentId], queryFn: getDevelopmentIdentity })
  const dashboard = useQuery({ queryKey: ['task-dashboard'], queryFn: getTaskDashboard, refetchInterval: 15_000 })
  const jobs = useQuery({
    queryKey: ['background-jobs', statusFilter],
    queryFn: () => getBackgroundJobs({ status: statusFilter || undefined }),
    refetchInterval: 15_000,
  })
  const replayChain = useQuery({
    queryKey: ['background-job-replay-chain', replayChainJobId],
    queryFn: () => getBackgroundJobReplayChain(replayChainJobId!),
    enabled: Boolean(replayChainJobId),
  })
  const scheduled = useQuery({
    queryKey: ['scheduled-actions', selectedAgentId],
    queryFn: () => getScheduledActions(),
    refetchInterval: 15_000,
  })
  const channels = useQuery({ queryKey: ['channels', selectedAgentId], queryFn: getChannelInstances, refetchInterval: 30_000 })
  const proactiveChannels = channels.data?.items.filter((item) => item.status === 'enabled' && item.implementation_status === 'ready' && item.capabilities.proactive_messages) ?? []
  const effectiveChannelId = channelId || proactiveChannels[0]?.id || ''
  const canManageTasks = session.data?.permissions.includes('task:manage') ?? false
  const canManageProactive = session.data?.permissions.includes('proactive:manage') ?? false

  const refresh = async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['task-dashboard']),
      invalidateAcrossTabs(queryClient, ['background-jobs']),
      invalidateAcrossTabs(queryClient, ['scheduled-actions']),
    ])
  }
  const cancelJob = useMutation({ mutationFn: cancelBackgroundJob, onSuccess: refresh })
  const replayJob = useMutation({
    mutationFn: (jobId: string) => replayBackgroundJob(jobId, '管理员确认问题已处理后安全重放'),
    onSuccess: refresh,
  })
  const cancelAction = useMutation({ mutationFn: cancelScheduledAction, onSuccess: refresh })
  const createAction = useMutation({
    mutationFn: () => createScheduledAction({
      user_id: identity.data!.user_id,
      conversation_id: null,
      kind: 'proactive_message',
      scheduled_for: new Date(scheduledFor).toISOString(),
      expires_at: new Date(new Date(scheduledFor).getTime() + 24 * 3600_000).toISOString(),
      idempotency_key: crypto.randomUUID(),
      reason,
      payload: {
        channel_id: effectiveChannelId,
        recipient_id: recipientId.trim(),
        message: message.trim(),
        importance: 0.75,
        confidence: 0.8,
        last_user_activity_at: new Date().toISOString(),
      },
      social_cost: 1,
    }),
    onSuccess: refresh,
  })

  const submitAction = (event: FormEvent) => {
    event.preventDefault()
    if (!identity.data || !reason.trim() || !scheduledFor || !effectiveChannelId || !recipientId.trim() || !message.trim()) return
    createAction.mutate()
  }
  const runCancel = useCallback((job: BackgroundJob) => {
    if (window.confirm(`确认取消任务 ${job.id}？运行中任务会设置协作式取消标记。`)) cancelJob.mutate(job.id)
  }, [cancelJob])
  const runReplay = useCallback((job: BackgroundJob) => {
    if (window.confirm(`确认重放任务 ${job.id}？原任务和尝试记录会完整保留。`)) replayJob.mutate(job.id)
  }, [replayJob])

  const jobColumns = useMemo<Array<AdminTableColumn<BackgroundJob>>>(() => [
    {
      key: 'job', label: '任务',
      render: (row) => <div className="table-primary"><strong>{backgroundJobKindLabels[row.kind]}</strong><code>{row.id}</code>{Object.keys(row.result_summary).length > 0 && <span className="audit-detail">{formatMetadataEntries(row.result_summary)}</span>}</div>,
    },
    {
      key: 'status', label: '状态',
      render: (row) => <span className={`entity-status task-${row.status}`}>{backgroundJobStatusLabels[row.status]}</span>,
    },
    { key: 'attempts', label: '尝试', render: (row) => `${row.attempt_count} / ${row.max_attempts}` },
    { key: 'queue', label: '队列', render: (row) => <code>{row.queue}</code> },
    { key: 'updated', label: '更新时间', render: (row) => new Date(row.updated_at).toLocaleString('zh-CN') },
    {
      key: 'actions', label: '操作',
      render: (row) => <div className="table-actions">
        <button disabled={!canManageTasks || !['pending', 'retrying', 'running'].includes(row.status)} onClick={() => runCancel(row)}><XCircle size={12} />取消</button>
        <button disabled={!canManageTasks || !['failed', 'dead_letter', 'canceled'].includes(row.status)} onClick={() => runReplay(row)}><ListRestart size={12} />重放</button>
        <button onClick={() => setReplayChainJobId(row.id)}><GitBranch size={12} />来源链</button>
      </div>,
    },
  ], [canManageTasks, runCancel, runReplay])
  const replayChainColumns = useMemo<Array<AdminTableColumn<BackgroundJobReplayChainItem>>>(() => [
    { key: 'task', label: '任务', render: (row) => <div className="table-primary"><strong>{backgroundJobKindLabels[row.kind]}</strong><code>{row.id}</code></div> },
    { key: 'status', label: '状态', render: (row) => <span className={`entity-status task-${row.status}`}>{backgroundJobStatusLabels[row.status]}</span> },
    { key: 'error', label: '安全错误码', render: (row) => row.last_error_code ? <code>{row.last_error_code}</code> : '无' },
    { key: 'source', label: '来源任务', render: (row) => row.replayed_from_id ? <code>{row.replayed_from_id}</code> : '原始任务' },
    { key: 'created', label: '创建时间', render: (row) => new Date(row.created_at).toLocaleString('zh-CN') },
    { key: 'completed', label: '完成时间', render: (row) => row.completed_at ? new Date(row.completed_at).toLocaleString('zh-CN') : '未完成' },
  ], [])
  const actionColumns = useMemo<Array<AdminTableColumn<ScheduledAction>>>(() => [
    {
      key: 'reason', label: '主动行为',
      render: (row) => <div className="table-primary"><strong>{scheduledActionKindLabels[row.kind]} · {row.reason}</strong><code>{row.id}</code></div>,
    },
    { key: 'status', label: '策略状态', render: (row) => <span className={`entity-status task-${row.status}`}>{scheduledActionStatusLabels[row.status]}</span> },
    { key: 'time', label: '计划时间', render: (row) => new Date(row.scheduled_for).toLocaleString('zh-CN') },
    { key: 'score', label: '评分 / 预算', render: (row) => `${row.score?.toFixed(3) ?? '待评估'} / ${row.social_cost}` },
    { key: 'decision', label: '决策依据', render: (row) => row.decision_reasons.join('、') || '等待任务进程评估' },
    {
      key: 'actions', label: '操作',
      render: (row) => <div className="table-actions"><button disabled={!canManageProactive || row.status !== 'pending'} onClick={() => window.confirm('确认取消该定时行为？') && cancelAction.mutate(row.id)}><XCircle size={12} />取消</button></div>,
    },
  ], [canManageProactive, cancelAction])
  const jobSearch = useCallback((row: BackgroundJob) => `${row.id} ${row.kind} ${row.status} ${row.queue} ${row.last_error_code ?? ''}`, [])
  const actionSearch = useCallback((row: ScheduledAction) => `${row.id} ${row.reason} ${row.status} ${row.decision_reasons.join(' ')}`, [])
  const data = dashboard.data
  const operationError = cancelJob.error ?? replayJob.error ?? cancelAction.error ?? createAction.error

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">可靠异步执行与主动行为</p>
          <h1>任务与主动行为</h1>
          <p>PostgreSQL 保存任务真相；Dramatiq/Redis 负责投递，重复消息由租约、去重键和尝试记录吸收。</p>
        </div>
        <span className="phase-tag">每 15 秒刷新</span>
      </section>

      {(dashboard.isError || jobs.isError || scheduled.isError || channels.isError) && <div className="notice error">任务数据读取失败，请检查 API、迁移与数据库连接。</div>}
      {operationError && <div className="notice error">{operationError.message}</div>}
      <section className="metric-grid" aria-label="任务指标">
        <article className="metric-card"><div className="metric-icon"><ListTodo size={18} /></div><p>待处理 / 重试</p><strong>{data ? `${data.pending} / ${data.retrying}` : '—'}</strong><span>由 Outbox 恢复投递</span></article>
        <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>执行中</p><strong>{data?.running ?? '—'}</strong><span>受数据库租约保护</span></article>
        <article className="metric-card"><div className="metric-icon"><ShieldAlert size={18} /></div><p>死信</p><strong>{data?.dead_letters ?? '—'}</strong><span>可审计安全重放</span></article>
        <article className="metric-card"><div className="metric-icon"><ServerCog size={18} /></div><p>在线任务进程</p><strong>{data?.workers.length ?? '—'}</strong><span>{data?.workers[0]?.worker_id ?? '最近 45 秒无心跳'}</span></article>
      </section>

      <div className="notice info"><Clock3 size={17} /><div><strong>主动消息默认关闭，当前只分发到安全边界</strong><span>执行时会重新检查最新配置、安静时段、用户活跃度、关系边界、评分阈值与每日社交预算，再交给统一渠道适配器发送。</span></div></div>

      <section className="panel task-create-panel">
        <div className="panel-heading"><div><span>主动行为</span><h2>新建定时候选</h2></div></div>
        <form className="task-create-form" onSubmit={submitAction}>
          <label><span>出站渠道</span><select value={effectiveChannelId} onChange={(event) => setChannelId(event.target.value)} required><option value="">选择已启用的 IM 渠道</option>{proactiveChannels.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.display_name}</option>)}</select></label>
          <label><span>收件人标识</span><input value={recipientId} maxLength={255} placeholder="Telegram chat_id 或 Web 用户标识" onChange={(event) => setRecipientId(event.target.value)} required /></label>
          <label><span>消息正文</span><textarea value={message} maxLength={20_000} rows={3} onChange={(event) => setMessage(event.target.value)} required /></label>
          <label><span>执行时间</span><input type="datetime-local" value={scheduledFor} onChange={(event) => setScheduledFor(event.target.value)} required /></label>
          <label><span>可审计原因</span><input value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} required /></label>
          <button className="primary-button" type="submit" disabled={!canManageProactive || !identity.data || !effectiveChannelId || !recipientId.trim() || !message.trim() || createAction.isPending}><Plus size={14} />创建候选</button>
        </form>
      </section>

      <section className="panel table-panel task-table-panel">
        <div className="panel-heading task-panel-heading"><div><span>执行真相</span><h2>后台任务</h2></div><label className="status-filter">状态<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as BackgroundJobStatus | '')}><option value="">全部</option>{Object.entries(backgroundJobStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
        <AdminDataTable rows={jobs.data?.items ?? []} columns={jobColumns} rowKey={(row) => row.id} searchableText={jobSearch} searchPlaceholder="搜索任务 ID、类型、队列或错误码" emptyMessage={jobs.isLoading ? '正在读取任务…' : '暂无匹配任务'} />
      </section>

      {replayChainJobId && <section className="panel table-panel task-table-panel" aria-label="任务重放来源链">
        <div className="panel-heading task-panel-heading"><div><span>安全摘要 · 不含任务载荷</span><h2>重放来源链</h2></div><button className="secondary-button" type="button" onClick={() => setReplayChainJobId(null)}>关闭</button></div>
        {replayChain.isError && <div className="notice error">来源链读取失败，请检查任务是否仍属于当前租户。</div>}
        <AdminDataTable rows={replayChain.data?.items ?? []} columns={replayChainColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.id} ${row.kind} ${row.status} ${row.last_error_code ?? ''}`} searchPlaceholder="搜索来源任务或错误码" emptyMessage={replayChain.isLoading ? '正在读取来源链…' : '没有可展示的来源链'} />
      </section>}

      <section className="panel table-panel task-table-panel">
        <div className="panel-heading task-panel-heading"><div><span>社交策略</span><h2>定时行为</h2></div><small>待调度 {data?.scheduled ?? '—'}</small></div>
        <AdminDataTable rows={scheduled.data?.items ?? []} columns={actionColumns} rowKey={(row) => row.id} searchableText={actionSearch} searchPlaceholder="搜索主动行为原因或决策依据" emptyMessage={scheduled.isLoading ? '正在读取定时行为…' : '暂无定时行为'} />
      </section>
    </div>
  )
}
