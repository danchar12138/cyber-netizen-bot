import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  ArrowUpRight,
  CircleDollarSign,
  ListTodo,
  Settings2,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'

import {
  getAdminSession,
  getChannelInstances,
  getObservabilityDashboard,
  getSystemOverview,
  getTaskStatus,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import {
  channelHealthLabels,
  componentHealthLabels,
  componentLabels,
  displayLabel,
  observabilityUnitLabel,
} from '../displayLabels'

const numberFormatter = new Intl.NumberFormat('zh-CN')

function formatUsd(microusd: number) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(microusd / 1_000_000)
}

export function DashboardPage() {
  const selectedAgentId = useSelectedAgentId()
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canReadObservability = session.data?.permissions.includes('trace:read') ?? false
  const canReadChannels = session.data?.permissions.includes('channel:read') ?? false
  const overview = useQuery({
    queryKey: ['system-overview'],
    queryFn: getSystemOverview,
    refetchInterval: 30_000,
  })
  const tasks = useQuery({
    queryKey: ['task-status'],
    queryFn: getTaskStatus,
    refetchInterval: 15_000,
  })
  const observability = useQuery({
    queryKey: ['observability-dashboard', selectedAgentId],
    queryFn: getObservabilityDashboard,
    enabled: session.isSuccess && canReadObservability,
    refetchInterval: 30_000,
  })
  const channels = useQuery({
    queryKey: ['channel-instances', selectedAgentId],
    queryFn: getChannelInstances,
    enabled: session.isSuccess && canReadChannels,
    refetchInterval: 30_000,
  })

  const systemData = overview.data
  const taskData = tasks.data
  const telemetry = observability.data
  const channelItems = channels.data?.items ?? []
  const modelTokens = telemetry?.models.reduce(
    (sum, item) => sum + item.input_tokens + item.output_tokens,
    0,
  ) ?? 0
  const taskBacklog = telemetry?.queue.backlog
  const healthyChannels = channelItems.filter(
    (channel) => channel.health_status === 'healthy',
  ).length
  const attentionChannels = channelItems.filter(
    (channel) => ['degraded', 'not_configured'].includes(channel.health_status),
  ).length
  const hasPartialError = overview.isError
    || tasks.isError
    || (canReadObservability && observability.isError)
    || (canReadChannels && channels.isError)

  const metrics = [
    {
      label: '当前智能体运行成功率',
      value: telemetry ? `${telemetry.agent_runs.success_rate_percent.toFixed(2)}%` : '—',
      detail: canReadObservability
        ? `${telemetry?.agent_runs.completed_runs ?? 0} 成功 · ${telemetry?.agent_runs.unsuccessful_runs ?? 0} 未成功`
        : '当前角色无可观测数据权限',
      icon: ShieldCheck,
    },
    {
      label: '模型冻结估算成本',
      value: telemetry ? formatUsd(telemetry.total_estimated_cost_microusd) : '—',
      detail: canReadObservability
        ? `${numberFormatter.format(modelTokens)} 词元 · 当前智能体`
        : '当前角色无模型用量权限',
      icon: CircleDollarSign,
    },
    {
      label: '当前智能体任务积压',
      value: taskBacklog === undefined ? '—' : numberFormatter.format(taskBacklog),
      detail: telemetry
        ? `最老待执行任务已等待 ${numberFormatter.format(telemetry.queue.oldest_wait_seconds)} 秒`
        : '当前角色无任务指标权限',
      icon: ListTodo,
    },
    {
      label: '应用接口错误率',
      value: telemetry ? `${telemetry.api.error_rate_percent.toFixed(2)}%` : '—',
      detail: canReadObservability
        ? `${telemetry?.api.requests ?? 0} 请求 · ${telemetry?.api.server_errors ?? 0} 次 5xx`
        : '当前角色无应用接口指标权限',
      icon: Activity,
    },
  ]

  return (
    <div className="page dashboard-page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">运营控制面</p>
          <h1>系统状态、运行质量与风险，一页掌握。</h1>
          <p>租户运行状态与当前智能体的质量、成本和渠道指标会自动刷新；切换智能体后相关视图同步更新。</p>
        </div>
        <Link to="/agents" className="primary-button">管理智能体 <ArrowUpRight size={16} /></Link>
      </section>

      {hasPartialError && (
        <div className="notice warning" role="alert">
          部分运营数据暂时不可用，其余已成功加载的状态仍可继续查看。
        </div>
      )}

      <section className="metric-grid" aria-label="核心运营指标">
        {metrics.map(({ icon: Icon, label, value, detail }) => (
          <article className="metric-card" key={label}>
            <div className="metric-icon"><Icon size={18} /></div>
            <p>{label}</p>
            <strong>{value}</strong>
            <span>{detail}</span>
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <article className="panel system-map">
          <div className="panel-heading">
            <div><p className="eyebrow">服务健康</p><h2>运行依赖</h2></div>
            <Link to="/settings" className="panel-link">系统设置 <ArrowUpRight size={13} /></Link>
          </div>
          <div className="component-list">
            {(systemData?.components ?? [
              { name: 'api', status: 'not_checked' as const },
              { name: 'postgresql', status: 'not_checked' as const },
              { name: 'redis', status: 'not_checked' as const },
              { name: 'object_storage', status: 'not_checked' as const },
            ]).map((component) => (
              <div className="component-row" key={component.name}>
                <span className={`component-status ${component.status}`} />
                <div>
                  <strong>{displayLabel(componentLabels, component.name)}</strong>
                  <small>{displayLabel(componentHealthLabels, component.status)}</small>
                </div>
                <span className="latency">{component.detail ?? '状态正常'}</span>
              </div>
            ))}
          </div>
          <dl className="overview-counts" aria-label="租户资产摘要">
            <div><dt>活跃智能体</dt><dd>{systemData?.active_agents ?? '—'}</dd></div>
            <div><dt>活跃会话</dt><dd>{systemData?.active_conversations ?? '—'}</dd></div>
            <div><dt>配置定义</dt><dd>{systemData?.configuration_definitions ?? '—'}</dd></div>
            <div><dt>运行环境</dt><dd>{systemData?.environment ?? '—'}</dd></div>
          </dl>
        </article>

        <article className="panel alert-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">近期风险</p><h2>活动告警</h2></div>
            {canReadObservability
              ? <Link to="/observability" className="panel-link">全部指标 <ArrowUpRight size={13} /></Link>
              : <span className="subtle">权限受限</span>}
          </div>
          {!canReadObservability && <div className="empty-state">当前角色不能查看运行轨迹和活动告警。</div>}
          {canReadObservability && !telemetry?.alerts.length && <div className="empty-state">当前聚合窗口没有活动告警。</div>}
          <div className="alert-list compact">
            {telemetry?.alerts.slice(0, 4).map((alert) => (
              <article className={alert.severity} key={alert.code}>
                <TriangleAlert size={16} />
                <div>
                  <strong>{alert.title}</strong>
                  <p>{alert.summary}</p>
                  <small>当前 {alert.current_value.toFixed(2)} {observabilityUnitLabel(alert.unit)} · 阈值 {alert.threshold_value.toFixed(2)} {observabilityUnitLabel(alert.unit)}</small>
                </div>
              </article>
            ))}
          </div>
        </article>
      </section>

      <section className="dashboard-grid operational-grid">
        <article className="panel channel-overview">
          <div className="panel-heading">
            <div><p className="eyebrow">当前智能体</p><h2>渠道状态</h2></div>
            {canReadChannels
              ? <Link to="/channels" className="panel-link">管理渠道 <ArrowUpRight size={13} /></Link>
              : <span className="subtle">权限受限</span>}
          </div>
          {canReadChannels && (
            <div className="status-summary" role="group" aria-label="渠道健康摘要">
              <div><span>实例</span><strong>{channelItems.length}</strong></div>
              <div><span>健康</span><strong>{healthyChannels}</strong></div>
              <div><span>需处理</span><strong>{attentionChannels}</strong></div>
              <div><span>已停用</span><strong>{channelItems.filter((channel) => channel.status === 'disabled').length}</strong></div>
            </div>
          )}
          {!canReadChannels && <div className="empty-state">当前角色不能查看渠道实例。</div>}
          {canReadChannels && channelItems.length === 0 && <div className="empty-state">当前智能体尚未配置渠道实例。</div>}
          <div className="component-list">
            {channelItems.slice(0, 5).map((channel) => (
              <div className="component-row" key={channel.id}>
                <span className={`component-status ${channel.health_status}`} />
                <div><strong>{channel.name}</strong><small>{channel.display_name}</small></div>
                <span className="latency">{displayLabel(channelHealthLabels, channel.health_status)}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel task-overview">
          <div className="panel-heading">
            <div><p className="eyebrow">异步执行</p><h2>任务与任务进程</h2></div>
            <Link to="/tasks" className="panel-link">任务控制台 <ArrowUpRight size={13} /></Link>
          </div>
          <div className="component-list">
            <div className="component-row">
              <span className={`component-status ${taskData?.worker.status ?? 'not_checked'}`} />
              <div><strong>后台任务进程</strong><small>{displayLabel(componentHealthLabels, taskData?.worker.status ?? 'not_checked')}</small></div>
              <span className="latency">{taskData?.worker.detail ?? '等待心跳'}</span>
            </div>
          </div>
          <div className="status-summary task-summary" role="group" aria-label="任务状态摘要">
            <div><span>等待</span><strong>{taskData?.pending_jobs ?? '—'}</strong></div>
            <div><span>运行</span><strong>{taskData?.running_jobs ?? '—'}</strong></div>
            <div><span>重试</span><strong>{taskData?.retrying_jobs ?? '—'}</strong></div>
            <div><span>死信</span><strong>{taskData?.dead_letter_jobs ?? '—'}</strong></div>
            <div><span>计划行为</span><strong>{taskData?.scheduled_actions ?? '—'}</strong></div>
          </div>
          <div className="task-footnote"><Settings2 size={14} /><span>队列积压以 PostgreSQL 为真相源，任务进程心跳每 15 秒刷新。</span></div>
        </article>
      </section>
    </div>
  )
}
