import { useQuery } from '@tanstack/react-query'
import { Activity, Clock3, ListTodo, ServerCog } from 'lucide-react'

import { getTaskStatus } from '../api'

export function TaskStatusPage() {
  const status = useQuery({
    queryKey: ['task-status'],
    queryFn: getTaskStatus,
    refetchInterval: 15_000,
  })
  const data = status.data

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">异步执行基础状态</p>
          <h1>任务与主动行为</h1>
          <p>查看 Dramatiq 与 Redis 基础状态；任务明细、重试、死信和安全重放将在 P5 接入持久化追踪后开放。</p>
        </div>
        <span className="phase-tag">每 15 秒刷新</span>
      </section>

      {status.isError && <div className="notice error">任务状态读取失败，请检查 API 与 Redis 配置。</div>}
      <section className="metric-grid" aria-label="任务指标">
        <article className="metric-card"><div className="metric-icon"><ListTodo size={18} /></div><p>待处理任务</p><strong>{data?.pending_jobs ?? '—'}</strong><span>当前租户聚合值</span></article>
        <article className="metric-card"><div className="metric-icon"><ServerCog size={18} /></div><p>任务框架</p><strong className="metric-text">Dramatiq</strong><span>Redis 消息代理</span></article>
        <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>工作进程状态</p><strong className="metric-text">{data?.worker.status === 'healthy' ? '健康' : '待探测'}</strong><span>{data?.worker.detail ?? '正在读取'}</span></article>
        <article className="metric-card"><div className="metric-icon"><Clock3 size={18} /></div><p>队列</p><strong className="metric-text">{data?.queues.join('、') || '—'}</strong><span>已声明基础队列</span></article>
      </section>

      <div className="notice info">
        <Activity size={17} />
        <div><strong>当前页面不伪造工作进程在线状态</strong><span>在 P5 建立心跳与任务真相表前，工作进程明确显示为“待探测”。</span></div>
      </div>
    </div>
  )
}
