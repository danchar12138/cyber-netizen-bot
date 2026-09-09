import { useMutation, useQuery } from '@tanstack/react-query'
import { Activity, Search, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import { getAdminSession, getCognitiveRunTrace } from '../api'

export function ObservabilityPage() {
  const [runId, setRunId] = useState('')
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const trace = useMutation({ mutationFn: getCognitiveRunTrace })
  const canRead = session.data?.permissions.includes('trace:read') ?? false

  return (
    <div className="page">
      <section className="page-heading compact"><div><p className="eyebrow">可回放运行</p><h1>认知运行轨迹</h1><p>查看阶段摘要、上下文裁剪统计、行动候选、策略选择、情绪快照和模型尝试；不会展示隐藏思维链或请求正文。</p></div></section>
      <div className="notice info"><ShieldCheck size={17} /><div><strong>安全可观测边界</strong><span>轨迹保留可解释决策事实与版本，不保存模型隐藏推理、密钥或完整 Prompt。</span></div></div>
      <section className="panel trace-search"><label><Activity size={16} /><input aria-label="Agent Run ID" value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="输入 Agent Run UUID" /></label><button className="primary-button" disabled={!canRead || !runId.trim() || trace.isPending} onClick={() => trace.mutate(runId.trim())}><Search size={14} /> 查询轨迹</button></section>
      {trace.error && <div className="notice error">{trace.error.message}</div>}
      {trace.data && <div className="trace-grid">
        <section className="panel"><div className="panel-heading"><h2>认知阶段</h2><span className="subtle">{trace.data.steps.length} 步</span></div><div className="trace-list">{trace.data.steps.map((step) => <article key={step.sequence}><span>{step.sequence}</span><div><strong>{step.stage}</strong><p>{step.summary}</p><code>{JSON.stringify(step.detail)}</code></div></article>)}</div></section>
        <section className="panel"><div className="panel-heading"><h2>行动与人格状态</h2><span className="subtle">Run {trace.data.run_id.slice(0, 8)}</span></div>{trace.data.persona_state && <dl className="settings-list"><div><dt>人格版本</dt><dd>v{trace.data.persona_state.persona_version}</dd></div><div><dt>情绪效价</dt><dd>{trace.data.persona_state.valence.toFixed(3)}</dd></div><div><dt>唤醒度</dt><dd>{trace.data.persona_state.arousal.toFixed(3)}</dd></div><div><dt>社交能量</dt><dd>{trace.data.persona_state.social_energy.toFixed(3)}</dd></div></dl>}<div className="candidate-list">{trace.data.candidates.map((candidate) => <article className={candidate.selected ? 'selected' : ''} key={candidate.sequence}><strong>{candidate.action} · {(candidate.confidence * 100).toFixed(0)}%</strong><span>{candidate.selected ? '策略已选' : candidate.rejection_reason ?? '未选择'}</span><p>{candidate.reason_summary}</p></article>)}</div></section>
        <section className="panel model-invocations"><div className="panel-heading"><h2>模型尝试</h2><span className="subtle">{trace.data.model_invocations.length} 次</span></div>{trace.data.model_invocations.map((item) => <article key={`${item.purpose}-${item.attempt}`}><strong>{item.provider} / {item.model}</strong><span>{item.status} · 尝试 {item.attempt} · {item.latency_ms ?? 0} ms</span><small>用途 {item.purpose} · 输入 {item.input_tokens ?? '—'} · 输出 {item.output_tokens ?? '—'}</small></article>)}</section>
      </div>}
    </div>
  )
}
