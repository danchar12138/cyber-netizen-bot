import { useMutation, useQuery } from '@tanstack/react-query'
import { Activity, CircleDollarSign, Clock3, Search, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useState } from 'react'

import { getAdminSession, getCognitiveRunTrace, getObservabilityDashboard } from '../api'
import {
  cognitiveActionLabels,
  cognitiveStageLabels,
  displayLabel,
  modelInvocationStatusLabels,
  modelPurposeLabels,
} from '../displayLabels'

function formatUsd(microusd: number) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(microusd / 1_000_000)
}

export function ObservabilityPage() {
  const [runId, setRunId] = useState('')
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canRead = session.data?.permissions.includes('trace:read') ?? false
  const dashboard = useQuery({
    queryKey: ['observability-dashboard'],
    queryFn: getObservabilityDashboard,
    enabled: canRead,
    refetchInterval: 30_000,
  })
  const trace = useMutation({ mutationFn: getCognitiveRunTrace })
  const data = dashboard.data

  return (
    <div className="page">
      <section className="page-heading compact"><div><p className="eyebrow">SLO、成本与可回放运行</p><h1>可观测性与运行轨迹</h1><p>聚合 API、Agent、模型和队列健康，按已发布阈值形成告警，并保留安全认知回放。</p></div></section>
      <div className="notice info"><ShieldCheck size={17} /><div><strong>安全可观测边界</strong><span>指标与 Trace 不保存消息正文、完整 Prompt、隐藏推理、密钥、访问令牌或对象键。</span></div></div>

      {dashboard.isError && <div className="notice error" role="alert">无法读取可观测聚合，请检查 API、数据库和当前权限。</div>}
      <section className="metric-grid" aria-label="服务等级与成本指标">
        <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>API 错误率</p><strong>{data ? `${data.api.error_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.api.requests ?? 0} 次请求 · {data?.api.server_errors ?? 0} 次 5xx</span></article>
        <article className="metric-card"><div className="metric-icon"><Clock3 size={18} /></div><p>API P95 / P99</p><strong>{data ? `${data.api.latency.p95_ms} / ${data.api.latency.p99_ms} ms` : '—'}</strong><span>P50 {data?.api.latency.p50_ms ?? '—'} ms</span></article>
        <article className="metric-card"><div className="metric-icon"><ShieldCheck size={18} /></div><p>Agent 运行成功率</p><strong>{data ? `${data.agent_runs.success_rate_percent.toFixed(2)}%` : '—'}</strong><span>{data?.agent_runs.completed_runs ?? 0} 成功 · {data?.agent_runs.unsuccessful_runs ?? 0} 未成功</span></article>
        <article className="metric-card"><div className="metric-icon"><CircleDollarSign size={18} /></div><p>模型冻结估算成本</p><strong>{data ? formatUsd(data.total_estimated_cost_microusd) : '—'}</strong><span>{data?.models.reduce((sum, item) => sum + item.input_tokens + item.output_tokens, 0) ?? 0} Token</span></article>
      </section>

      <div className="observability-grid">
        <section className="panel alert-panel">
          <div className="panel-heading"><div><p className="eyebrow">确定性阈值</p><h2>活动告警</h2></div><span className="subtle">{data?.alerts.length ?? 0} 项</span></div>
          {!data?.alerts.length && <div className="empty-state">当前窗口没有活动告警。</div>}
          <div className="alert-list">{data?.alerts.map((alert) => <article className={alert.severity} key={alert.code}><TriangleAlert size={16} /><div><strong>{alert.title}</strong><p>{alert.summary}</p><small>当前 {alert.current_value.toFixed(2)} {alert.unit} · 阈值 {alert.threshold_value.toFixed(2)} {alert.unit}</small></div></article>)}</div>
        </section>
        <section className="panel queue-panel">
          <div className="panel-heading"><div><p className="eyebrow">PostgreSQL 真相源</p><h2>队列状态</h2></div></div>
          <dl className="settings-list"><div><dt>待处理 / 重试</dt><dd>{data?.queue.backlog ?? '—'} 项</dd></div><div><dt>最老任务等待</dt><dd>{data?.queue.oldest_wait_seconds ?? '—'} 秒</dd></div><div><dt>Agent P95 / P99</dt><dd>{data ? `${data.agent_runs.latency.p95_ms} / ${data.agent_runs.latency.p99_ms} ms` : '—'}</dd></div><div><dt>聚合窗口开始</dt><dd>{data ? new Date(data.window_started_at).toLocaleString('zh-CN') : '—'}</dd></div></dl>
        </section>
      </div>

      <section className="panel model-cost-panel">
        <div className="panel-heading"><div><p className="eyebrow">调用时价格快照</p><h2>模型用量与成本</h2></div><span className="subtle">按 Provider / 模型分组</span></div>
        {!data?.models.length && <div className="empty-state">当前窗口没有模型调用。</div>}
        <div className="model-cost-list">{data?.models.map((item) => <article key={`${item.provider}/${item.model}`}><div><strong>{item.provider} / {item.model}</strong><span>{item.invocations} 次调用 · {item.failed_invocations} 次失败</span></div><div><strong>{formatUsd(item.estimated_cost_microusd)}</strong><span>输入 {item.input_tokens} · 输出 {item.output_tokens} Token</span></div><div><strong>{item.latency.p95_ms} ms</strong><span>P95 · P99 {item.latency.p99_ms} ms</span></div></article>)}</div>
      </section>

      <section className="panel trace-search"><label><Activity size={16} /><input aria-label="Agent 运行 ID" value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="输入 Agent 运行 UUID" /></label><button className="primary-button" disabled={!canRead || !runId.trim() || trace.isPending} onClick={() => trace.mutate(runId.trim())}><Search size={14} /> 查询轨迹</button></section>
      {trace.error && <div className="notice error" role="alert">{trace.error.message}</div>}
      {trace.data && <div className="trace-grid">
        <section className="panel"><div className="panel-heading"><h2>认知阶段</h2><span className="subtle">{trace.data.steps.length} 步</span></div><div className="trace-list">{trace.data.steps.map((step) => <article key={step.sequence}><span>{step.sequence}</span><div><strong>{displayLabel(cognitiveStageLabels, step.stage)}</strong><p>{step.summary}</p><code>{JSON.stringify(step.detail)}</code></div></article>)}</div></section>
        <section className="panel"><div className="panel-heading"><h2>行动与人格状态</h2><span className="subtle">运行 {trace.data.run_id.slice(0, 8)}</span></div>{trace.data.persona_state && <dl className="settings-list"><div><dt>人格版本</dt><dd>v{trace.data.persona_state.persona_version}</dd></div><div><dt>情绪效价</dt><dd>{trace.data.persona_state.valence.toFixed(3)}</dd></div><div><dt>唤醒度</dt><dd>{trace.data.persona_state.arousal.toFixed(3)}</dd></div><div><dt>社交能量</dt><dd>{trace.data.persona_state.social_energy.toFixed(3)}</dd></div></dl>}<div className="candidate-list">{trace.data.candidates.map((candidate) => <article className={candidate.selected ? 'selected' : ''} key={candidate.sequence}><strong>{displayLabel(cognitiveActionLabels, candidate.action)} · {(candidate.confidence * 100).toFixed(0)}%</strong><span>{candidate.selected ? '策略已选' : candidate.rejection_reason ?? '未选择'}</span><p>{candidate.reason_summary}</p></article>)}</div></section>
        <section className="panel model-invocations"><div className="panel-heading"><h2>模型尝试</h2><span className="subtle">{trace.data.model_invocations.length} 次</span></div>{trace.data.model_invocations.map((item) => <article key={`${item.purpose}-${item.attempt}`}><strong>{item.provider} / {item.model}</strong><span>{modelInvocationStatusLabels[item.status]} · 尝试 {item.attempt} · {item.latency_ms ?? 0} ms · {formatUsd(item.estimated_cost_microusd)}</span><small>用途 {displayLabel(modelPurposeLabels, item.purpose)} · 输入 {item.input_tokens ?? '—'} · 输出 {item.output_tokens ?? '—'}</small></article>)}</section>
      </div>}
    </div>
  )
}
