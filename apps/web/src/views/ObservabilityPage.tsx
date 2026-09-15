import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, BellRing, CheckCircle2, ChevronDown, CircleDollarSign, ClipboardList, Clock3, Gauge, History, RefreshCw, Search, Send, ShieldAlert, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import {
  acknowledgeObservabilityAlert,
  batchDisposeObservabilityAlerts,
  clearObservabilityAlertDisposition,
  getAdminSession,
  getCognitiveRunTrace,
  getObservabilityAlertDispositionEvents,
  getObservabilityAlertLifecycleMetrics,
  getObservabilityAlertLifecyclePage,
  getObservabilityAlertOperationsSummary,
  getObservabilityAlertReplayMetrics,
  getObservabilityAlertReplayReviews,
  getObservabilityDashboard,
  suppressObservabilityAlert,
  type ObservabilityAlertLifecycle,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import {
  cognitiveActionLabels,
  cognitiveStageLabels,
  displayLabel,
  formatMetadataEntries,
  modelInvocationStatusLabels,
  modelPurposeLabels,
  observabilityBaselineMetricLabels,
  observabilityAlertCodeLabels,
  observabilityAlertSourceTypeLabels,
  observabilityHandoffReasonLabels,
  observabilityReplayDecisionLabels,
  observabilityReplayReasonLabels,
  observabilityUnitLabel,
} from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

function formatUsd(microusd: number) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(microusd / 1_000_000)
}

function formatDuration(seconds: number | null | undefined) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3_600) return `${Math.round(seconds / 60)} 分钟`
  return `${(seconds / 3_600).toFixed(1)} 小时`
}

export function ObservabilityPage() {
  const queryClient = useQueryClient()
  const [runId, setRunId] = useState('')
  const [alertStatus, setAlertStatus] = useState<'all' | 'active' | 'resolved'>('all')
  const [alertSourceType, setAlertSourceType] = useState('')
  const [alertSeverity, setAlertSeverity] = useState<'warning' | 'critical' | ''>('')
  const [minimumDurationMinutes, setMinimumDurationMinutes] = useState('')
  const [replayDecision, setReplayDecision] = useState<'allowed' | 'blocked' | ''>('')
  const [dispositionInputError, setDispositionInputError] = useState<string | null>(null)
  const [dispositionFeedback, setDispositionFeedback] = useState<string | null>(null)
  const [selectedLifecycleIds, setSelectedLifecycleIds] = useState<Set<string>>(new Set())
  const [historyLifecycle, setHistoryLifecycle] = useState<ObservabilityAlertLifecycle | null>(null)
  const selectedAgentId = useSelectedAgentId()
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canReadTrace = session.data?.permissions.includes('trace:read') ?? false
  const canManageAlerts = session.data?.permissions.includes('observability_alert:manage') ?? false
  useEffect(() => {
    setSelectedLifecycleIds(new Set())
    setHistoryLifecycle(null)
  }, [selectedAgentId, alertStatus, alertSourceType, alertSeverity, minimumDurationMinutes])
  const dashboard = useQuery({
    queryKey: ['observability-dashboard', selectedAgentId],
    queryFn: getObservabilityDashboard,
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const lifecycleMetrics = useQuery({
    queryKey: ['observability-alert-lifecycle-metrics', selectedAgentId, 1_440, 60, alertSourceType, alertSeverity],
    queryFn: () => getObservabilityAlertLifecycleMetrics({
      window_minutes: 1_440,
      bucket_minutes: 60,
      source_type: alertSourceType || undefined,
      severity: alertSeverity || undefined,
    }),
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const alertOperations = useQuery({
    queryKey: ['observability-alert-operations-summary', selectedAgentId],
    queryFn: getObservabilityAlertOperationsSummary,
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const alertLifecycles = useInfiniteQuery({
    queryKey: [
      'observability-alert-lifecycles',
      selectedAgentId,
      alertStatus,
      alertSourceType,
      alertSeverity,
      minimumDurationMinutes,
    ],
    queryFn: ({ pageParam }) => getObservabilityAlertLifecyclePage({
      status: alertStatus === 'all' ? undefined : alertStatus,
      source_type: alertSourceType || undefined,
      severity: alertSeverity || undefined,
      minimum_duration_minutes: minimumDurationMinutes
        ? Number(minimumDurationMinutes)
        : undefined,
      cursor: pageParam ?? undefined,
      limit: 50,
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const replayMetrics = useQuery({
    queryKey: ['observability-alert-replay-metrics', selectedAgentId, 1_440, alertSourceType],
    queryFn: () => getObservabilityAlertReplayMetrics({
      window_minutes: 1_440,
      source_type: alertSourceType || undefined,
    }),
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const replayReviews = useInfiniteQuery({
    queryKey: ['observability-alert-replay-reviews', selectedAgentId, alertSourceType, replayDecision],
    queryFn: ({ pageParam }) => getObservabilityAlertReplayReviews({
      source_type: alertSourceType || undefined,
      decision: replayDecision || undefined,
      cursor: pageParam ?? undefined,
      limit: 25,
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const dispositionHistory = useQuery({
    queryKey: ['observability-alert-disposition-events', selectedAgentId, historyLifecycle?.id],
    queryFn: () => getObservabilityAlertDispositionEvents({ lifecycle_id: historyLifecycle!.id }),
    enabled: canReadTrace && historyLifecycle !== null,
  })
  const refreshAlertViews = useCallback(async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['observability-alert-lifecycles']),
      invalidateAcrossTabs(queryClient, ['observability-dashboard']),
      invalidateAcrossTabs(queryClient, ['observability-alert-disposition-events']),
      invalidateAcrossTabs(queryClient, ['observability-alert-operations-summary']),
      invalidateAcrossTabs(queryClient, ['observability-alert-replay-metrics']),
      invalidateAcrossTabs(queryClient, ['observability-alert-replay-reviews']),
      invalidateAcrossTabs(queryClient, ['audit-records']),
    ])
  }, [queryClient])
  const dispositionMutation = useMutation({
    mutationFn: ({ action, lifecycleId, reason, expiresAt }: {
      action: 'acknowledge' | 'suppress' | 'clear'
      lifecycleId: string
      reason?: string
      expiresAt?: string
    }) => {
      if (action === 'acknowledge') {
        return acknowledgeObservabilityAlert(lifecycleId, { confirmed: true, reason: reason! })
      }
      if (action === 'suppress') {
        return suppressObservabilityAlert(lifecycleId, {
          confirmed: true,
          reason: reason!,
          expires_at: expiresAt!,
        })
      }
      return clearObservabilityAlertDisposition(lifecycleId, { confirmed: true })
    },
    onSuccess: async (result) => {
      const statusLabel = result.status === 'acknowledged'
        ? '已确认'
        : result.status === 'suppressed' ? '已抑制' : '已解除处置'
      setDispositionFeedback(`${displayLabel(observabilityAlertCodeLabels, result.code)}${statusLabel}`)
      await refreshAlertViews()
    },
  })
  const batchDispositionMutation = useMutation({
    mutationFn: ({
      action,
      reason,
      expiresAt,
    }: {
      action: 'acknowledge' | 'suppress' | 'clear'
      reason: string
      expiresAt?: string
    }) => batchDisposeObservabilityAlerts({
      lifecycle_ids: Array.from(selectedLifecycleIds),
      action,
      reason,
      expires_at: expiresAt,
      confirmed: true,
    }),
    onSuccess: async (result) => {
      setDispositionFeedback(`已完成 ${result.items.length} 条通用告警批量处置`)
      setSelectedLifecycleIds(new Set())
      await refreshAlertViews()
    },
  })
  const trace = useMutation({ mutationFn: getCognitiveRunTrace })
  const data = dashboard.data
  const lifecycle = lifecycleMetrics.data
  const operations = alertOperations.data
  const anomalousSignals = operations?.baseline.signals.filter((item) => item.anomalous) ?? []
  const lifecycleRows = alertLifecycles.data?.pages.flatMap((page) => page.items) ?? []
  const replay = replayMetrics.data
  const replayRows = replayReviews.data?.pages.flatMap((page) => page.items) ?? []
  const trendMaximum = Math.max(
    1,
    ...(lifecycle?.trend.map((point) => point.opened + point.resolved + point.escalated) ?? []),
  )
  const runDisposition = useCallback((item: ObservabilityAlertLifecycle, action: 'acknowledge' | 'suppress') => {
    setDispositionInputError(null)
    setDispositionFeedback(null)
    const reason = window.prompt(
      action === 'suppress' ? '请输入临时抑制原因' : '请输入告警确认备注',
      item.disposition_reason ?? '',
    )?.trim()
    if (!reason) return

    let expiresAt: string | undefined
    if (action === 'suppress') {
      const suggestedTime = new Date(Date.now() + 60 * 60_000).toISOString()
      const input = window.prompt(
        '请输入抑制到期时间（ISO 8601，必须包含时区）',
        suggestedTime,
      )?.trim()
      if (!input) return
      const parsed = new Date(input)
      if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        setDispositionInputError('抑制到期时间必须是未来的有效时间')
        return
      }
      expiresAt = parsed.toISOString()
    }

    const title = displayLabel(observabilityAlertCodeLabels, item.code)
    if (!window.confirm(`确认${action === 'suppress' ? '临时抑制' : '标记已确认'}“${title}”？`)) return
    dispositionMutation.mutate({ action, lifecycleId: item.id, reason, expiresAt })
  }, [dispositionMutation])
  const clearDisposition = useCallback((item: ObservabilityAlertLifecycle) => {
    setDispositionInputError(null)
    setDispositionFeedback(null)
    const title = displayLabel(observabilityAlertCodeLabels, item.code)
    if (window.confirm(`确认解除“${title}”的当前处置？`)) {
      dispositionMutation.mutate({ action: 'clear', lifecycleId: item.id })
    }
  }, [dispositionMutation])
  const runBatchDisposition = useCallback((action: 'acknowledge' | 'suppress' | 'clear') => {
    setDispositionInputError(null)
    setDispositionFeedback(null)
    if (!selectedLifecycleIds.size) return
    const reason = window.prompt(
      action === 'acknowledge'
        ? '请输入批量确认备注'
        : action === 'suppress' ? '请输入批量抑制原因' : '请输入批量解除原因',
    )?.trim()
    if (!reason) return
    let expiresAt: string | undefined
    if (action === 'suppress') {
      const input = window.prompt(
        '请输入抑制到期时间（ISO 8601，必须包含时区）',
        new Date(Date.now() + 60 * 60_000).toISOString(),
      )?.trim()
      if (!input) return
      const parsed = new Date(input)
      if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        setDispositionInputError('抑制到期时间必须是未来的有效时间')
        return
      }
      expiresAt = parsed.toISOString()
    }
    if (!window.confirm(`确认批量处置 ${selectedLifecycleIds.size} 条通用告警？`)) return
    batchDispositionMutation.mutate({ action, reason, expiresAt })
  }, [batchDispositionMutation, selectedLifecycleIds])
  const visibleLifecycleIds = lifecycleRows.map((item) => item.id)
  const selectedLifecycles = lifecycleRows.filter(
    (item) => selectedLifecycleIds.has(item.id),
  ) ?? []
  const canBatchSetDisposition = selectedLifecycles.length === selectedLifecycleIds.size
    && selectedLifecycles.every((item) => item.status === 'active')
  const canBatchClearDisposition = selectedLifecycles.length === selectedLifecycleIds.size
    && selectedLifecycles.every((item) => item.disposition_status !== null)
  const allVisibleSelected = visibleLifecycleIds.length > 0
    && visibleLifecycleIds.every((id) => selectedLifecycleIds.has(id))
  const toggleAllVisible = () => {
    setSelectedLifecycleIds((current) => {
      const next = new Set(current)
      for (const id of visibleLifecycleIds) {
        if (allVisibleSelected) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }
  const toggleLifecycle = (id: string) => {
    setSelectedLifecycleIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="page">
      <section className="page-heading compact"><div><p className="eyebrow">服务等级、成本与可回放运行</p><h1>可观测性与运行轨迹</h1><p>聚合当前智能体的应用接口、模型和队列健康，按已发布阈值形成告警，并保留安全认知回放。</p></div></section>
      <div className="notice info"><ShieldCheck size={17} /><div><strong>安全可观测边界</strong><span>指标与链路追踪不保存消息正文、完整提示词、隐藏推理、密钥、访问令牌或对象键。</span></div></div>

      {dashboard.isError && <div className="notice error" role="alert">无法读取可观测聚合，请检查应用接口、数据库和当前权限。</div>}
      {lifecycleMetrics.isError && <div className="notice error" role="alert">无法读取通用告警生命周期指标，请检查当前 Agent 与迁移状态。</div>}
      {alertOperations.isError && <div className="notice error" role="alert">无法读取告警异常基线与值班交接摘要。</div>}
      {alertLifecycles.isError && <div className="notice error" role="alert">无法读取通用告警生命周期，请检查当前 Agent 与迁移状态。</div>}
      {(replayMetrics.isError || replayReviews.isError) && <div className="notice error" role="alert">无法读取通知重放复核记录，请检查当前 Agent 与迁移状态。</div>}
      {(dispositionInputError || dispositionMutation.error || batchDispositionMutation.error) && <div className="notice error" role="alert">{dispositionInputError ?? dispositionMutation.error?.message ?? batchDispositionMutation.error?.message}</div>}
      {dispositionFeedback && <div className="notice success" role="status"><CheckCircle2 size={17} /><div><strong>告警处置已更新</strong><span>{dispositionFeedback}</span></div></div>}
      <section className="metric-grid" aria-label="服务等级与成本指标">
        <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>应用接口错误率</p><strong>{data ? `${data.api.error_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.api.requests ?? 0} 次请求 · {data?.api.server_errors ?? 0} 次服务端错误</span></article>
        <article className="metric-card"><div className="metric-icon"><Clock3 size={18} /></div><p>应用接口 P95 / P99</p><strong>{data ? `${data.api.latency.p95_ms} / ${data.api.latency.p99_ms} 毫秒` : '—'}</strong><span>P50 {data?.api.latency.p50_ms ?? '—'} 毫秒</span></article>
        <article className="metric-card"><div className="metric-icon"><ShieldCheck size={18} /></div><p>智能体运行成功率</p><strong>{data ? `${data.agent_runs.success_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.agent_runs.completed_runs ?? 0} 成功 · {data?.agent_runs.unsuccessful_runs ?? 0} 未成功</span></article>
        <article className="metric-card"><div className="metric-icon"><CircleDollarSign size={18} /></div><p>模型冻结估算成本</p><strong>{data ? formatUsd(data.total_estimated_cost_microusd) : '—'}</strong><span>{data?.models.reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0) ?? 0} 词元</span></article>
        <article className="metric-card"><div className="metric-icon"><Send size={18} /></div><p>渠道出站失败率</p><strong>{data ? `${data.channel_delivery.failure_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.channel_delivery.attempts ?? 0} 次尝试 · 成功 {data?.channel_delivery.delivered ?? 0}</span></article>
        <article className="metric-card"><div className="metric-icon"><BellRing size={18} /></div><p>通知投递任务</p><strong>{data?.notification_delivery.total ?? '—'}</strong><span>成功 {data?.notification_delivery.succeeded ?? 0} · 重试 {data?.notification_delivery.retrying ?? 0} · 待处理死信 {data?.notification_delivery.dead_letters ?? 0}</span></article>
      </section>

      <section className="alert-operations-section" aria-label="告警异常基线与值班交接">
        <div className="panel-heading channel-operation-heading"><div><p className="eyebrow">稳健基线 · {operations?.baseline.window_minutes ?? '—'} 分钟交接窗口</p><h2>值班交接摘要</h2></div><span className="subtle">{operations ? new Date(operations.generated_at).toLocaleString('zh-CN') : '正在聚合'}</span></div>
        <div className="metric-grid alert-handoff-metrics">
          <article className="metric-card"><div className="metric-icon"><TriangleAlert size={18} /></div><p>活动告警</p><strong>{operations?.handoff.active ?? '—'}</strong><span>{operations?.handoff.critical_active ?? 0} 条严重</span></article>
          <article className="metric-card"><div className="metric-icon"><ClipboardList size={18} /></div><p>待确认</p><strong>{operations?.handoff.unacknowledged_active ?? '—'}</strong><span>已确认 {operations?.handoff.acknowledged_active ?? 0} · 抑制 {operations?.handoff.suppressed_active ?? 0}</span></article>
          <article className="metric-card"><div className="metric-icon"><Gauge size={18} /></div><p>基线异常</p><strong>{anomalousSignals.length || 0}</strong><span>{operations?.baseline.periods ?? '—'} 个历史周期 · 灵敏度 {operations?.baseline.sensitivity ?? '—'}</span></article>
          <article className="metric-card"><div className="metric-icon"><ShieldAlert size={18} /></div><p>阻止重放</p><strong>{operations?.handoff.blocked_replays ?? '—'}</strong><span>开启 {operations?.handoff.opened ?? 0} · 恢复 {operations?.handoff.resolved ?? 0} · 升级 {operations?.handoff.escalated ?? 0}</span></article>
        </div>
        <div className="alert-operations-grid">
          <section className="panel baseline-signal-panel">
            <div className="panel-heading"><div><p className="eyebrow">中位数 + MAD</p><h2>异常基线信号</h2></div><span className="subtle">{anomalousSignals.length} 项偏离</span></div>
            {!anomalousSignals.length && <div className="empty-state">当前开启量与升级量未超过稳健历史基线。</div>}
            {!!anomalousSignals.length && <div className="baseline-signal-list">{anomalousSignals.map((item) => <article key={`${item.source_type ?? 'all'}-${item.metric}`}><div><strong>{displayLabel(observabilityBaselineMetricLabels, item.metric)}</strong><span>{item.source_type ? displayLabel(observabilityAlertSourceTypeLabels, item.source_type) : '全部来源'}</span></div><div><strong>{item.current_value}</strong><span>阈值 {item.threshold_value.toFixed(2)} · 中位数 {item.baseline_median.toFixed(2)} · MAD {item.baseline_mad.toFixed(2)}</span></div></article>)}</div>}
          </section>
          <section className="panel handoff-priority-panel">
            <div className="panel-heading"><div><p className="eyebrow">最多 10 项</p><h2>优先关注</h2></div></div>
            {!operations?.handoff.priority_items.length && <div className="empty-state">当前没有需要交接的活动告警。</div>}
            {!!operations?.handoff.priority_items.length && <ol className="handoff-priority-list">{operations.handoff.priority_items.map((item) => <li key={item.lifecycle_id}><span className={`severity-label ${item.severity}`}>{item.severity === 'critical' ? '严重' : '警告'}</span><div><strong>{displayLabel(observabilityAlertCodeLabels, item.code)}</strong><small>{displayLabel(observabilityAlertSourceTypeLabels, item.source_type)} · 持续 {formatDuration(item.active_minutes * 60)} · L{item.escalation_level}</small><span>{item.reason_codes.map((reason) => displayLabel(observabilityHandoffReasonLabels, reason)).join(' · ')}</span></div><button className="icon-button" title="下钻该来源" aria-label={`下钻 ${displayLabel(observabilityAlertCodeLabels, item.code)} 来源`} onClick={() => { setAlertSourceType(item.source_type); setAlertStatus('active') }}><Search size={13} /></button></li>)}</ol>}
          </section>
        </div>
        {!!operations?.handoff.sources.length && <section className="panel handoff-source-panel"><div className="panel-heading"><div><p className="eyebrow">交接窗口</p><h2>来源汇总</h2></div></div><div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>来源</th><th>活动</th><th>严重</th><th>待确认</th><th>开启</th><th>恢复</th><th>升级</th><th>下钻</th></tr></thead><tbody>{operations.handoff.sources.map((item) => <tr key={item.source_type}><td><strong>{displayLabel(observabilityAlertSourceTypeLabels, item.source_type)}</strong></td><td>{item.active}</td><td>{item.critical_active}</td><td>{item.unacknowledged_active}</td><td>{item.opened}</td><td>{item.resolved}</td><td>{item.escalated}</td><td><button className="icon-button" title="下钻该来源" aria-label={`下钻 ${displayLabel(observabilityAlertSourceTypeLabels, item.source_type)} 来源`} onClick={() => setAlertSourceType(item.source_type)}><Search size={13} /></button></td></tr>)}</tbody></table></div></section>}
      </section>

      <section className="channel-lifecycle-observability" aria-label="通用告警生命周期指标">
        <div className="panel-heading channel-operation-heading"><div><p className="eyebrow">通用来源 · 最近 24 小时 · 每小时固定桶</p><h2>告警生命周期趋势</h2></div><span className="subtle">当前 Agent</span></div>
        <div className="metric-grid lifecycle-observability-metrics">
          <article className="metric-card"><div className="metric-icon"><TriangleAlert size={18} /></div><p>活动事件</p><strong>{lifecycle?.active ?? '—'}</strong><span>当前仍未恢复</span></article>
          <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>新开启</p><strong>{lifecycle?.opened ?? '—'}</strong><span>窗口内首次发生</span></article>
          <article className="metric-card"><div className="metric-icon"><ShieldCheck size={18} /></div><p>已恢复</p><strong>{lifecycle?.resolved ?? '—'}</strong><span>窗口内完成恢复</span></article>
          <article className="metric-card"><div className="metric-icon"><BellRing size={18} /></div><p>已升级</p><strong>{lifecycle?.escalated ?? '—'}</strong><span>窗口内升级通知</span></article>
          <article className="metric-card"><div className="metric-icon"><Clock3 size={18} /></div><p>平均恢复耗时</p><strong>{lifecycle ? `${Math.round(lifecycle.mean_recovery_seconds / 60)} 分钟` : '—'}</strong><span>仅统计已恢复事件</span></article>
          <article className="metric-card"><div className="metric-icon"><Clock3 size={18} /></div><p>P95 恢复耗时</p><strong>{lifecycle ? `${Math.round(lifecycle.p95_recovery_seconds / 60)} 分钟` : '—'}</strong><span>仅统计已恢复事件</span></article>
        </div>
        <div className="panel lifecycle-trend-panel">
          <div className="lifecycle-trend-legend"><span><i className="opened" />开启</span><span><i className="resolved" />恢复</span><span><i className="escalated" />升级</span></div>
          {!lifecycle?.trend.length && <div className="empty-state">当前窗口暂无生命周期变化。</div>}
          {lifecycle?.trend.length ? <div className="lifecycle-trend" role="img" aria-label="每小时告警开启、恢复与升级数量趋势">
            {lifecycle.trend.map((point) => <div className="lifecycle-trend-bucket" key={point.bucket_started_at} title={`${new Date(point.bucket_started_at).toLocaleString('zh-CN')}：开启 ${point.opened}，恢复 ${point.resolved}，升级 ${point.escalated}`}>
              <div className="lifecycle-trend-bars"><i className="opened" style={{ height: `${Math.max(point.opened ? 8 : 0, point.opened / trendMaximum * 100)}%` }} /><i className="resolved" style={{ height: `${Math.max(point.resolved ? 8 : 0, point.resolved / trendMaximum * 100)}%` }} /><i className="escalated" style={{ height: `${Math.max(point.escalated ? 8 : 0, point.escalated / trendMaximum * 100)}%` }} /></div>
              <time dateTime={point.bucket_started_at}>{new Date(point.bucket_started_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time>
            </div>)}
          </div> : null}
        </div>
        <div className="panel observability-source-metrics">
          <div className="panel-heading"><div><p className="eyebrow">按来源独立聚合</p><h2>来源健康对比</h2></div><span className="subtle">{lifecycle?.sources.length ?? 0} 个来源</span></div>
          {!lifecycle?.sources.length && <div className="empty-state">当前筛选条件下暂无来源数据。</div>}
          {!!lifecycle?.sources.length && <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>来源</th><th>活动</th><th>开启</th><th>恢复</th><th>升级</th><th>平均恢复</th><th>P95 恢复</th><th>下钻</th></tr></thead><tbody>{lifecycle.sources.map((item) => <tr key={item.source_type} className={alertSourceType === item.source_type ? 'selected-row' : ''}><td className="table-primary"><strong>{displayLabel(observabilityAlertSourceTypeLabels, item.source_type)}</strong><code>{item.source_type}</code></td><td>{item.active}</td><td>{item.opened}</td><td>{item.resolved}</td><td>{item.escalated}</td><td>{formatDuration(Math.round(item.mean_recovery_seconds))}</td><td>{formatDuration(item.p95_recovery_seconds)}</td><td><button className="icon-button" title="下钻该来源" aria-label={`下钻 ${displayLabel(observabilityAlertSourceTypeLabels, item.source_type)} 生命周期`} onClick={() => setAlertSourceType(item.source_type)}><Search size={13} /></button></td></tr>)}</tbody></table></div>}
        </div>
      </section>

      <section className="panel table-panel observability-lifecycle-table">
        <div className="admin-table-toolbar">
          <div><p className="eyebrow">当前 Agent</p><h2>通用告警生命周期</h2></div>
          <div className="table-actions lifecycle-filters">
            <label className="status-filter"><span>状态</span><select value={alertStatus} onChange={(event) => setAlertStatus(event.target.value as typeof alertStatus)}><option value="all">全部状态</option><option value="active">活动</option><option value="resolved">已恢复</option></select></label>
            <label className="status-filter"><span>来源</span><select value={alertSourceType} onChange={(event) => setAlertSourceType(event.target.value)}><option value="">全部来源</option>{Object.entries(observabilityAlertSourceTypeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label className="status-filter"><span>级别</span><select value={alertSeverity} onChange={(event) => setAlertSeverity(event.target.value as typeof alertSeverity)}><option value="">全部级别</option><option value="warning">警告</option><option value="critical">严重</option></select></label>
            <label className="status-filter"><span>最短持续</span><select value={minimumDurationMinutes} onChange={(event) => setMinimumDurationMinutes(event.target.value)}><option value="">不限</option><option value="15">15 分钟</option><option value="60">1 小时</option><option value="360">6 小时</option><option value="1440">24 小时</option></select></label>
            <small>{lifecycleRows.length} 条</small>
          </div>
        </div>
        <div className="alert-batch-toolbar" aria-label="批量告警处置">
          <span>已选择 <strong>{selectedLifecycleIds.size}</strong> 条</span>
          <div className="table-actions">
            <button disabled={!selectedLifecycleIds.size || batchDispositionMutation.isPending} onClick={() => setSelectedLifecycleIds(new Set())}><X size={13} />取消选择</button>
            <button disabled={!canManageAlerts || !selectedLifecycleIds.size || !canBatchSetDisposition || batchDispositionMutation.isPending} onClick={() => runBatchDisposition('acknowledge')}><CheckCircle2 size={13} />批量确认</button>
            <button disabled={!canManageAlerts || !selectedLifecycleIds.size || !canBatchSetDisposition || batchDispositionMutation.isPending} onClick={() => runBatchDisposition('suppress')}><ShieldCheck size={13} />批量抑制</button>
            <button disabled={!canManageAlerts || !selectedLifecycleIds.size || !canBatchClearDisposition || batchDispositionMutation.isPending} onClick={() => runBatchDisposition('clear')}><RefreshCw size={13} />批量解除</button>
          </div>
        </div>
        <div className="admin-table-scroll">
          <table className="admin-table">
            <thead><tr><th className="selection-cell"><input type="checkbox" aria-label="选择当前全部告警" checked={allVisibleSelected} onChange={toggleAllVisible} /></th><th>来源</th><th>状态</th><th>当前 / 阈值</th><th>升级</th><th>发生时间</th><th>恢复</th><th>处置</th><th>操作</th></tr></thead>
            <tbody>{lifecycleRows.map((item) => <tr key={item.id}>
              <td className="selection-cell"><input type="checkbox" aria-label={`选择 ${displayLabel(observabilityAlertCodeLabels, item.code)}`} checked={selectedLifecycleIds.has(item.id)} onChange={() => toggleLifecycle(item.id)} /></td>
              <td className="table-primary"><strong>{displayLabel(observabilityAlertCodeLabels, item.code)}</strong><small>{displayLabel(observabilityAlertSourceTypeLabels, item.source_type)}</small><code>{item.source_key}</code></td>
              <td><span className={`entity-status ${item.status}`}>{item.status === 'active' ? '活动' : '已恢复'}</span><small className={`severity-label ${item.severity}`}>{item.severity === 'critical' ? '严重' : '警告'}</small></td>
              <td><strong>{item.current_value.toFixed(2)} {observabilityUnitLabel(item.unit)}</strong><small>阈值 {item.threshold_value.toFixed(2)} {observabilityUnitLabel(item.unit)} · {item.occurrences} 次评估</small></td>
              <td><strong>L{item.escalation_level}</strong><small>{item.last_escalated_at ? new Date(item.last_escalated_at).toLocaleString('zh-CN') : '尚未升级'}</small></td>
              <td><strong>{new Date(item.first_occurred_at).toLocaleString('zh-CN')}</strong><small>最近 {new Date(item.last_occurred_at).toLocaleString('zh-CN')}</small></td>
              <td><strong>{formatDuration(item.recovery_duration_seconds)}</strong><small>{item.resolved_at ? new Date(item.resolved_at).toLocaleString('zh-CN') : '等待恢复'}</small></td>
              <td>{item.disposition_status
                ? <div className="table-primary disposition-summary"><span className={`entity-status disposition-${item.disposition_status}`}>{item.disposition_status === 'suppressed' ? '已抑制' : '已确认'}</span><small>{item.disposition_reason}</small>{item.disposition_expires_at && <small>到期 {new Date(item.disposition_expires_at).toLocaleString('zh-CN')}</small>}</div>
                : <span className="subtle">未处置</span>}</td>
              <td><div className="table-actions disposition-actions">
                {item.status === 'active' && item.disposition_status !== 'acknowledged' && <button disabled={!canManageAlerts || dispositionMutation.isPending} onClick={() => runDisposition(item, 'acknowledge')}><CheckCircle2 size={12} />确认</button>}
                {item.status === 'active' && item.disposition_status !== 'suppressed' && <button disabled={!canManageAlerts || dispositionMutation.isPending} onClick={() => runDisposition(item, 'suppress')}><ShieldCheck size={12} />抑制</button>}
                {item.disposition_status && <button disabled={!canManageAlerts || dispositionMutation.isPending} onClick={() => clearDisposition(item)}><RefreshCw size={12} />解除</button>}
                <button title="查看处置历史" aria-label={`查看 ${displayLabel(observabilityAlertCodeLabels, item.code)} 的处置历史`} onClick={() => setHistoryLifecycle(item)}><History size={12} /></button>
              </div></td>
            </tr>)}</tbody>
          </table>
          {!lifecycleRows.length && <div className="admin-table-empty">当前筛选条件下没有通用告警生命周期。</div>}
        </div>
        {alertLifecycles.hasNextPage && <div className="table-pagination"><button className="secondary-button" disabled={alertLifecycles.isFetchingNextPage} onClick={() => void alertLifecycles.fetchNextPage()}><ChevronDown size={14} />{alertLifecycles.isFetchingNextPage ? '正在加载' : '加载更多生命周期'}</button></div>}
      </section>

      {historyLifecycle && <section className="panel alert-history-panel" aria-label="通用告警处置历史">
        <div className="panel-heading"><div><p className="eyebrow">追加式处置记录</p><h2>{displayLabel(observabilityAlertCodeLabels, historyLifecycle.code)} · 处置历史</h2></div><button className="icon-button" title="关闭处置历史" aria-label="关闭处置历史" onClick={() => setHistoryLifecycle(null)}><X size={15} /></button></div>
        {dispositionHistory.isError && <div className="notice error">无法读取处置历史，请检查迁移与当前权限。</div>}
        {dispositionHistory.isLoading && <div className="empty-state">正在读取处置历史…</div>}
        {!dispositionHistory.isLoading && !dispositionHistory.data?.length && <div className="empty-state">该生命周期尚无处置记录。</div>}
        {!!dispositionHistory.data?.length && <ol className="alert-history-timeline">{dispositionHistory.data.map((event) => <li key={event.id}><span className={`entity-status disposition-${event.action}`}>{event.action === 'acknowledged' ? '已确认' : event.action === 'suppressed' ? '已抑制' : '已解除'}</span><div><strong>{event.reason}</strong><small>{new Date(event.occurred_at).toLocaleString('zh-CN')} · 操作者 {event.actor_id.slice(0, 8)}</small>{event.expires_at && <small>抑制到期 {new Date(event.expires_at).toLocaleString('zh-CN')}</small>}</div></li>)}</ol>}
      </section>}

      <section className="replay-review-section" aria-label="通知重放复核运营">
        <div className="panel-heading channel-operation-heading"><div><p className="eyebrow">安全审计 · 最近 24 小时</p><h2>通知重放复核运营</h2></div><label className="status-filter"><span>结论</span><select value={replayDecision} onChange={(event) => setReplayDecision(event.target.value as typeof replayDecision)}><option value="">全部结论</option><option value="allowed">允许重放</option><option value="blocked">阻止重放</option></select></label></div>
        <div className="metric-grid replay-review-metrics">
          <article className="metric-card"><div className="metric-icon"><History size={18} /></div><p>复核总数</p><strong>{replay?.total ?? '—'}</strong><span>当前来源筛选窗口</span></article>
          <article className="metric-card"><div className="metric-icon"><ShieldCheck size={18} /></div><p>允许重放</p><strong>{replay?.allowed ?? '—'}</strong><span>{replay ? `${replay.allowed_rate_percent.toFixed(2)}% 允许率` : '等待统计'}</span></article>
          <article className="metric-card"><div className="metric-icon"><ShieldAlert size={18} /></div><p>阻止重放</p><strong>{replay?.blocked ?? '—'}</strong><span>不会创建新任务</span></article>
          <article className="metric-card"><div className="metric-icon"><BellRing size={18} /></div><p>涉及来源</p><strong>{replay?.sources.length ?? '—'}</strong><span>按安全来源键聚合</span></article>
        </div>
        <div className="replay-review-summary-grid">
          <section className="panel">
            <div className="panel-heading"><div><p className="eyebrow">稳定原因码</p><h2>复核原因分布</h2></div></div>
            {!replay?.reasons.length && <div className="empty-state">当前窗口暂无复核结果。</div>}
            {!!replay?.reasons.length && <dl className="settings-list">{replay.reasons.map((item) => <div key={item.reason_code}><dt>{displayLabel(observabilityReplayReasonLabels, item.reason_code)}</dt><dd>{item.count} 次</dd></div>)}</dl>}
          </section>
          <section className="panel">
            <div className="panel-heading"><div><p className="eyebrow">允许 / 阻止</p><h2>复核来源分布</h2></div></div>
            {!replay?.sources.length && <div className="empty-state">当前窗口暂无来源数据。</div>}
            {!!replay?.sources.length && <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>来源</th><th>总数</th><th>允许</th><th>阻止</th></tr></thead><tbody>{replay.sources.map((item) => <tr key={item.source_type ?? 'unknown'}><td>{item.source_type ? displayLabel(observabilityAlertSourceTypeLabels, item.source_type) : '来源缺失'}</td><td>{item.total}</td><td>{item.allowed}</td><td>{item.blocked}</td></tr>)}</tbody></table></div>}
          </section>
        </div>
        <section className="panel table-panel replay-review-table">
          <div className="admin-table-toolbar"><div><p className="eyebrow">追加式安全记录</p><h2>近期复核事件</h2></div><span className="subtle">已加载 {replayRows.length} 条</span></div>
          <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>结论</th><th>原因</th><th>来源</th><th>源任务</th><th>操作者</th><th>复核时间</th></tr></thead><tbody>{replayRows.map((item) => <tr key={item.id}><td><span className={`entity-status replay-${item.decision}`}>{displayLabel(observabilityReplayDecisionLabels, item.decision)}</span></td><td className="table-primary"><strong>{displayLabel(observabilityReplayReasonLabels, item.reason_code)}</strong>{item.suppression_expires_at && <small>抑制到期 {new Date(item.suppression_expires_at).toLocaleString('zh-CN')}</small>}</td><td className="table-primary"><strong>{item.source_type ? displayLabel(observabilityAlertSourceTypeLabels, item.source_type) : '来源缺失'}</strong><code>{item.source_key ?? '—'}</code></td><td><code>{item.source_job_id?.slice(0, 8) ?? '已归档'}</code></td><td><code>{item.actor_id.slice(0, 8)}</code></td><td>{new Date(item.reviewed_at).toLocaleString('zh-CN')}</td></tr>)}</tbody></table>{!replayRows.length && <div className="admin-table-empty">当前筛选条件下没有重放复核事件。</div>}</div>
          {replayReviews.hasNextPage && <div className="table-pagination"><button className="secondary-button" disabled={replayReviews.isFetchingNextPage} onClick={() => void replayReviews.fetchNextPage()}><ChevronDown size={14} />{replayReviews.isFetchingNextPage ? '正在加载' : '加载更多复核记录'}</button></div>}
        </section>
      </section>

      <div className="observability-grid">
        <section className="panel alert-panel">
          <div className="panel-heading"><div><p className="eyebrow">确定性阈值</p><h2>活动告警</h2></div><span className="subtle">{data?.alerts.length ?? 0} 项</span></div>
          {!data?.alerts.length && <div className="empty-state">当前窗口没有活动告警。</div>}
          <div className="alert-list">{data?.alerts.map((alert) => <article className={alert.severity} key={alert.code}><TriangleAlert size={16} /><div><strong>{alert.title}</strong><p>{alert.summary}</p><small>当前 {alert.current_value.toFixed(2)} {observabilityUnitLabel(alert.unit)} · 阈值 {alert.threshold_value.toFixed(2)} {observabilityUnitLabel(alert.unit)}</small></div></article>)}</div>
        </section>
        <section className="panel queue-panel">
          <div className="panel-heading"><div><p className="eyebrow">PostgreSQL 真相源</p><h2>队列状态</h2></div></div>
          <dl className="settings-list"><div><dt>待处理 / 重试</dt><dd>{data?.queue.backlog ?? '—'} 项</dd></div><div><dt>最老任务等待</dt><dd>{data?.queue.oldest_wait_seconds ?? '—'} 秒</dd></div><div><dt>智能体 P95 / P99</dt><dd>{data ? `${data.agent_runs.latency.p95_ms} / ${data.agent_runs.latency.p99_ms} ms` : '—'}</dd></div><div><dt>聚合窗口开始</dt><dd>{data ? new Date(data.window_started_at).toLocaleString('zh-CN') : '—'}</dd></div></dl>
        </section>
      </div>

      <section className="panel model-cost-panel">
        <div className="panel-heading"><div><p className="eyebrow">调用时价格快照</p><h2>模型用量与成本</h2></div><span className="subtle">按模型服务与模型分组</span></div>
        {!data?.models.length && <div className="empty-state">当前窗口没有模型调用。</div>}
        <div className="model-cost-list">{data?.models.map((item) => <article key={`${item.provider}/${item.model}`}><div><strong>{item.provider} / {item.model}</strong><span>{item.invocations} 次调用 · {item.failed_invocations} 次失败</span></div><div><strong>{formatUsd(item.estimated_cost_microusd)}</strong><span>输入 {item.input_tokens} · 输出 {item.output_tokens} 词元</span></div><div><strong>{item.latency.p95_ms} 毫秒</strong><span>P95 · P99 {item.latency.p99_ms} 毫秒</span></div></article>)}</div>
      </section>

      <section className="panel trace-search"><label><Activity size={16} /><input aria-label="智能体运行 ID" value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="输入智能体运行 UUID" /></label><button className="primary-button" disabled={!canReadTrace || !runId.trim() || trace.isPending} onClick={() => trace.mutate(runId.trim())}><Search size={14} /> 查询轨迹</button></section>
      {trace.error && <div className="notice error" role="alert">{trace.error.message}</div>}
      {trace.data && <div className="trace-grid">
        <section className="panel"><div className="panel-heading"><h2>认知阶段</h2><span className="subtle">{trace.data.steps.length} 步</span></div><div className="trace-list">{trace.data.steps.map((step) => <article key={step.sequence}><span>{step.sequence}</span><div><strong>{displayLabel(cognitiveStageLabels, step.stage)}</strong><p>{step.summary}</p><code>{formatMetadataEntries(step.detail)}</code></div></article>)}</div></section>
        <section className="panel"><div className="panel-heading"><h2>行动与人格状态</h2><span className="subtle">运行 {trace.data.run_id.slice(0, 8)}</span></div>{trace.data.persona_state && <dl className="settings-list"><div><dt>人格版本</dt><dd>v{trace.data.persona_state.persona_version}</dd></div><div><dt>情绪效价</dt><dd>{trace.data.persona_state.valence.toFixed(3)}</dd></div><div><dt>唤醒度</dt><dd>{trace.data.persona_state.arousal.toFixed(3)}</dd></div><div><dt>社交能量</dt><dd>{trace.data.persona_state.social_energy.toFixed(3)}</dd></div></dl>}<div className="candidate-list">{trace.data.candidates.map((candidate) => <article className={candidate.selected ? 'selected' : ''} key={candidate.sequence}><strong>{displayLabel(cognitiveActionLabels, candidate.action)} · {(candidate.confidence * 100).toFixed(0)}%</strong><span>{candidate.selected ? '策略已选' : candidate.rejection_reason ?? '未选择'}</span><p>{candidate.reason_summary}</p></article>)}</div></section>
        <section className="panel model-invocations"><div className="panel-heading"><h2>模型尝试</h2><span className="subtle">{trace.data.model_invocations.length} 次</span></div>{trace.data.model_invocations.map((item) => <article key={`${item.purpose}-${item.attempt}`}><strong>{item.provider} / {item.model}</strong><span>{modelInvocationStatusLabels[item.status]} · 尝试 {item.attempt} · {item.latency_ms ?? 0} ms · {formatUsd(item.estimated_cost_microusd)}</span><small>用途 {displayLabel(modelPurposeLabels, item.purpose)} · 输入 {item.input_tokens ?? '—'} · 输出 {item.output_tokens ?? '—'}</small></article>)}</section>
      </div>}
    </div>
  )
}
