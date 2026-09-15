import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, BellRing, CheckCircle2, CircleDollarSign, Clock3, RefreshCw, Search, Send, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useCallback, useState } from 'react'

import {
  acknowledgeObservabilityAlert,
  clearObservabilityAlertDisposition,
  getAdminSession,
  getChannelAlertLifecycleMetrics,
  getCognitiveRunTrace,
  getObservabilityAlertLifecycles,
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
  observabilityAlertCodeLabels,
  observabilityAlertSourceTypeLabels,
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
  const [dispositionInputError, setDispositionInputError] = useState<string | null>(null)
  const [dispositionFeedback, setDispositionFeedback] = useState<string | null>(null)
  const selectedAgentId = useSelectedAgentId()
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canReadTrace = session.data?.permissions.includes('trace:read') ?? false
  const canReadChannels = session.data?.permissions.includes('channel:read') ?? false
  const canManageAlerts = session.data?.permissions.includes('observability_alert:manage') ?? false
  const dashboard = useQuery({
    queryKey: ['observability-dashboard', selectedAgentId],
    queryFn: getObservabilityDashboard,
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const lifecycleMetrics = useQuery({
    queryKey: ['channel-alert-lifecycle-metrics', selectedAgentId, 1_440, 60],
    queryFn: () => getChannelAlertLifecycleMetrics(1_440, 60),
    enabled: canReadChannels,
    refetchInterval: 30_000,
  })
  const alertLifecycles = useQuery({
    queryKey: [
      'observability-alert-lifecycles',
      selectedAgentId,
      alertStatus,
      alertSourceType,
      alertSeverity,
      minimumDurationMinutes,
    ],
    queryFn: () => getObservabilityAlertLifecycles({
      status: alertStatus === 'all' ? undefined : alertStatus,
      source_type: alertSourceType || undefined,
      severity: alertSeverity || undefined,
      minimum_duration_minutes: minimumDurationMinutes
        ? Number(minimumDurationMinutes)
        : undefined,
    }),
    enabled: canReadTrace,
    refetchInterval: 30_000,
  })
  const refreshAlertViews = useCallback(async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['observability-alert-lifecycles']),
      invalidateAcrossTabs(queryClient, ['observability-dashboard']),
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
  const trace = useMutation({ mutationFn: getCognitiveRunTrace })
  const data = dashboard.data
  const lifecycle = lifecycleMetrics.data
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

  return (
    <div className="page">
      <section className="page-heading compact"><div><p className="eyebrow">服务等级、成本与可回放运行</p><h1>可观测性与运行轨迹</h1><p>聚合当前智能体的应用接口、模型和队列健康，按已发布阈值形成告警，并保留安全认知回放。</p></div></section>
      <div className="notice info"><ShieldCheck size={17} /><div><strong>安全可观测边界</strong><span>指标与链路追踪不保存消息正文、完整提示词、隐藏推理、密钥、访问令牌或对象键。</span></div></div>

      {dashboard.isError && <div className="notice error" role="alert">无法读取可观测聚合，请检查应用接口、数据库和当前权限。</div>}
      {lifecycleMetrics.isError && <div className="notice error" role="alert">无法读取告警生命周期指标，请检查渠道读取权限与迁移状态。</div>}
      {alertLifecycles.isError && <div className="notice error" role="alert">无法读取通用告警生命周期，请检查当前 Agent 与迁移状态。</div>}
      {(dispositionInputError || dispositionMutation.error) && <div className="notice error" role="alert">{dispositionInputError ?? dispositionMutation.error?.message}</div>}
      {dispositionFeedback && <div className="notice success" role="status"><CheckCircle2 size={17} /><div><strong>告警处置已更新</strong><span>{dispositionFeedback}</span></div></div>}
      <section className="metric-grid" aria-label="服务等级与成本指标">
        <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>应用接口错误率</p><strong>{data ? `${data.api.error_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.api.requests ?? 0} 次请求 · {data?.api.server_errors ?? 0} 次服务端错误</span></article>
        <article className="metric-card"><div className="metric-icon"><Clock3 size={18} /></div><p>应用接口 P95 / P99</p><strong>{data ? `${data.api.latency.p95_ms} / ${data.api.latency.p99_ms} 毫秒` : '—'}</strong><span>P50 {data?.api.latency.p50_ms ?? '—'} 毫秒</span></article>
        <article className="metric-card"><div className="metric-icon"><ShieldCheck size={18} /></div><p>智能体运行成功率</p><strong>{data ? `${data.agent_runs.success_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.agent_runs.completed_runs ?? 0} 成功 · {data?.agent_runs.unsuccessful_runs ?? 0} 未成功</span></article>
        <article className="metric-card"><div className="metric-icon"><CircleDollarSign size={18} /></div><p>模型冻结估算成本</p><strong>{data ? formatUsd(data.total_estimated_cost_microusd) : '—'}</strong><span>{data?.models.reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0) ?? 0} 词元</span></article>
        <article className="metric-card"><div className="metric-icon"><Send size={18} /></div><p>渠道出站失败率</p><strong>{data ? `${data.channel_delivery.failure_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.channel_delivery.attempts ?? 0} 次尝试 · 成功 {data?.channel_delivery.delivered ?? 0}</span></article>
        <article className="metric-card"><div className="metric-icon"><BellRing size={18} /></div><p>通知投递任务</p><strong>{data?.notification_delivery.total ?? '—'}</strong><span>成功 {data?.notification_delivery.succeeded ?? 0} · 重试 {data?.notification_delivery.retrying ?? 0} · 待处理死信 {data?.notification_delivery.dead_letters ?? 0}</span></article>
      </section>

      <section className="channel-lifecycle-observability" aria-label="告警生命周期指标">
        <div className="panel-heading channel-operation-heading"><div><p className="eyebrow">最近 24 小时 · 每小时固定桶</p><h2>告警生命周期趋势</h2></div><span className="subtle">当前 Agent</span></div>
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
      </section>

      <section className="panel table-panel observability-lifecycle-table">
        <div className="admin-table-toolbar">
          <div><p className="eyebrow">当前 Agent</p><h2>通用告警生命周期</h2></div>
          <div className="table-actions lifecycle-filters">
            <label className="status-filter"><span>状态</span><select value={alertStatus} onChange={(event) => setAlertStatus(event.target.value as typeof alertStatus)}><option value="all">全部状态</option><option value="active">活动</option><option value="resolved">已恢复</option></select></label>
            <label className="status-filter"><span>来源</span><select value={alertSourceType} onChange={(event) => setAlertSourceType(event.target.value)}><option value="">全部来源</option>{Object.entries(observabilityAlertSourceTypeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label className="status-filter"><span>级别</span><select value={alertSeverity} onChange={(event) => setAlertSeverity(event.target.value as typeof alertSeverity)}><option value="">全部级别</option><option value="warning">警告</option><option value="critical">严重</option></select></label>
            <label className="status-filter"><span>最短持续</span><select value={minimumDurationMinutes} onChange={(event) => setMinimumDurationMinutes(event.target.value)}><option value="">不限</option><option value="15">15 分钟</option><option value="60">1 小时</option><option value="360">6 小时</option><option value="1440">24 小时</option></select></label>
            <small>{alertLifecycles.data?.length ?? 0} 条</small>
          </div>
        </div>
        <div className="admin-table-scroll">
          <table className="admin-table">
            <thead><tr><th>来源</th><th>状态</th><th>当前 / 阈值</th><th>升级</th><th>发生时间</th><th>恢复</th><th>处置</th><th>操作</th></tr></thead>
            <tbody>{alertLifecycles.data?.map((item) => <tr key={item.id}>
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
              </div></td>
            </tr>)}</tbody>
          </table>
          {!alertLifecycles.data?.length && <div className="admin-table-empty">当前筛选条件下没有通用告警生命周期。</div>}
        </div>
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
