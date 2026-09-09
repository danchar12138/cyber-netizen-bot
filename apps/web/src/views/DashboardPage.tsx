import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowUpRight, Bot, ListTodo, Settings2 } from 'lucide-react'

import { getSystemOverview } from '../api'

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
          <p className="eyebrow">SYSTEM PULSE</p>
          <h1>早上好，心智系统正在等待唤醒。</h1>
          <p>在一个控制面中管理 Agent、模型、记忆、渠道与运行策略。</p>
        </div>
        <button className="primary-button">创建第一个 Agent <ArrowUpRight size={16} /></button>
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
            <div><p className="eyebrow">RUNTIME</p><h2>系统组件</h2></div>
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
                <div><strong>{component.name}</strong><small>{component.status}</small></div>
                <span className="latency">等待探测</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel roadmap-card">
          <div className="panel-heading">
            <div><p className="eyebrow">DEVELOPMENT</p><h2>P0 工程基线</h2></div>
            <span className="phase-tag">IN PROGRESS</span>
          </div>
          <div className="progress-track"><span /></div>
          <p className="progress-copy">管理面骨架和配置注册表已经接通，持久化发布流程将在下一切片实现。</p>
          <ul className="phase-list">
            <li className="done">uv workspace 与包边界</li>
            <li className="done">FastAPI 管理接口</li>
            <li className="done">React 管理后台骨架</li>
            <li>数据库与服务深度健康检查</li>
            <li>配置草稿、发布与回滚</li>
          </ul>
        </article>
      </section>
    </div>
  )
}

