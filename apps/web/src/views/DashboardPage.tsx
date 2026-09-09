import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Activity, ArrowUpRight, Bot, ListTodo, Settings2 } from 'lucide-react'

import { getSystemOverview } from '../api'

const fallbackMetrics = [
  { label: '活跃 Agent', value: '—', icon: Bot },
  { label: '活跃会话', value: '—', icon: Activity },
  { label: '待处理任务', value: '—', icon: ListTodo },
  { label: '配置定义', value: '—', icon: Settings2 },
]

const componentLabels: Record<string, string> = {
  api: '应用接口',
  postgresql: 'PostgreSQL 数据库',
  redis: 'Redis 任务服务',
  object_storage: 'MinIO 对象存储',
}

const healthLabels: Record<string, string> = {
  healthy: '健康',
  ready: '就绪',
  degraded: '异常',
  not_checked: '待探测',
  not_configured: '未配置',
}

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
                <div><strong>{componentLabels[component.name] ?? component.name}</strong><small>{healthLabels[component.status] ?? component.status}</small></div>
                <span className="latency">等待探测</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel roadmap-card">
          <div className="panel-heading">
            <div><p className="eyebrow">开发进度</p><h2>P2 管理后台</h2></div>
            <span className="phase-tag">已完成</span>
          </div>
          <div className="progress-track"><span className="complete" /></div>
          <p className="progress-copy">配置、权限、资源、会话、MinIO 附件、基础管理页与跨标签页同步已经接通。</p>
          <ul className="phase-list">
            <li className="done">uv workspace 与包边界</li>
            <li className="done">FastAPI 管理接口</li>
            <li className="done">React 管理后台骨架</li>
            <li className="done">数据库与服务深度健康检查</li>
            <li className="done">配置草稿、发布与回滚</li>
            <li className="done">内部对话与断线事件恢复</li>
            <li className="done">生产模型凭证与后台配置</li>
            <li className="done">角色权限与资源审计</li>
            <li className="done">完整会话管理与 MinIO 附件</li>
            <li className="done">响应式与无障碍基础</li>
            <li className="done">基础管理页收口</li>
          </ul>
        </article>
      </section>
    </div>
  )
}
