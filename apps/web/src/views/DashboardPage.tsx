import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Activity, ArrowUpRight, Bot, ListTodo, Settings2 } from 'lucide-react'

import { getSystemOverview } from '../api'
import { componentHealthLabels, componentLabels, displayLabel } from '../displayLabels'

const fallbackMetrics = [
  { label: '活跃 Agent', value: '—', icon: Bot },
  { label: '活跃会话', value: '—', icon: Activity },
  { label: '待处理任务', value: '—', icon: ListTodo },
  { label: '配置定义', value: '—', icon: Settings2 },
]

export function DashboardPage() {
  const overview = useQuery({ queryKey: ['system-overview'], queryFn: getSystemOverview })
  const data = overview.data
  const metrics = data
    ? [
        { label: '活跃 Agent', value: data.active_agents, icon: Bot },
        { label: '活跃会话', value: data.active_conversations, icon: Activity },
        { label: '待处理任务', value: data.pending_jobs, icon: ListTodo },
        { label: '配置定义', value: data.configuration_definitions, icon: Settings2 },
      ]
    : fallbackMetrics

  return (
    <div className="page dashboard-page">
      <section className="page-heading">
        <div>
          <p className="eyebrow">系统脉搏</p>
          <h1>早上好，心智系统正在等待唤醒。</h1>
          <p>在一个控制面中管理 Agent、模型、记忆、渠道与运行策略。</p>
        </div>
        <Link to="/agents" className="primary-button">管理 Agent <ArrowUpRight size={16} /></Link>
      </section>

      {overview.isError && (
        <div className="notice warning">
          API 尚未连接。启动 `uv run poe dev-api` 后，实时状态会自动出现在这里。
        </div>
      )}

      <section className="metric-grid">
        {metrics.map(({ icon: Icon, label, value }) => (
          <article className="metric-card" key={label}>
            <div className="metric-icon"><Icon size={18} /></div>
            <p>{label}</p>
            <strong>{value}</strong>
            <span>实时聚合指标</span>
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <article className="panel system-map">
          <div className="panel-heading">
            <div><p className="eyebrow">运行时</p><h2>系统组件</h2></div>
            <span className="subtle">v{data?.version ?? '0.1.0'}</span>
          </div>
          <div className="component-list">
            {(data?.components ?? [
              { name: 'api', status: 'not_checked' as const },
              { name: 'postgresql', status: 'not_checked' as const },
              { name: 'redis', status: 'not_checked' as const },
              { name: 'object_storage', status: 'not_checked' as const },
            ]).map((component) => (
              <div className="component-row" key={component.name}>
                <span className={`component-status ${component.status}`} />
                <div><strong>{displayLabel(componentLabels, component.name)}</strong><small>{displayLabel(componentHealthLabels, component.status)}</small></div>
                <span className="latency">等待探测</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel roadmap-card">
          <div className="panel-heading">
            <div><p className="eyebrow">能力验收</p><h2>核心系统能力</h2></div>
            <span className="phase-tag">主要能力可用</span>
          </div>
          <div className="progress-track"><span className="complete" /></div>
          <p className="progress-copy">认知、记忆、主动行为、渠道、安全治理与生产发布链路已经接通，并持续接受自动回归验证。</p>
          <ul className="phase-list">
            <li className="done">可恢复的流式内部对话</li>
            <li className="done">版本化拟人认知与策略门</li>
            <li className="done">长期记忆、关系与混合召回</li>
            <li className="done">异步反思和受控主动行为</li>
            <li className="done">多模态渠道协议与能力协商</li>
            <li className="done">配置、密钥、权限与审计治理</li>
            <li className="done">数据生命周期和隔离恢复验证</li>
            <li className="done">拟人回归、可观测性与发布供应链</li>
          </ul>
        </article>
      </section>
    </div>
  )
}
