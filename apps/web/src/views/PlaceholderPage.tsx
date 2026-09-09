import { Construction, MoveRight } from 'lucide-react'

const names: Record<string, string> = {
  agents: 'Agent 与人格',
  models: '模型与路由',
  prompts: 'Prompt 与上下文',
  memories: '记忆与关系',
  users: '用户与身份',
  tools: '工具与策略',
  channels: '渠道与适配器',
  tasks: '任务与主动行为',
  evaluations: '评测实验室',
  observability: '运行轨迹',
  audit: '审计日志',
  settings: '系统设置',
}

export function PlaceholderPage({ section }: { section: string }) {
  const title = names[section] ?? '管理模块'
  return (
    <div className="page">
      <section className="page-heading compact">
        <div><p className="eyebrow">MANAGEMENT MODULE</p><h1>{title}</h1><p>管理入口已经预留，将按开发计划逐步接入真实数据与操作。</p></div>
      </section>
      <div className="panel placeholder-panel">
        <div className="placeholder-icon"><Construction size={25} /></div>
        <h2>{title}将在后续纵向切片实现</h2>
        <p>页面不会用无效按钮伪装尚未实现的后端能力。对应 Schema、API、权限和审计接通后再开放操作。</p>
        <a href="/configuration">查看已接通的配置注册表 <MoveRight size={15} /></a>
      </div>
    </div>
  )
}

