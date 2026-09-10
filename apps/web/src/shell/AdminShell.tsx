import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  Bot,
  BrainCircuit,
  Cable,
  ChevronDown,
  CircleGauge,
  DatabaseBackup,
  FlaskConical,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  ListChecks,
  LogOut,
  MemoryStick,
  MessageCircleMore,
  MessagesSquare,
  ScrollText,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Users,
  Wrench,
} from 'lucide-react'
import { useEffect, useMemo, type ComponentType, type ReactNode } from 'react'

import { getAdminSession, getManagedAgents } from '../api'
import { setSelectedAgentId, useSelectedAgentId } from '../agentSelection'
import { useAuthentication } from '../components/authentication-context'
import { adminRoleLabels } from '../displayLabels'

interface NavigationItem {
  label: string
  path: string
  icon: ComponentType<{ size?: number; strokeWidth?: number }>
}

const primaryNavigation: NavigationItem[] = [
  { label: '运行总览', path: '/', icon: CircleGauge },
  { label: '内部对话', path: '/chat', icon: MessageCircleMore },
  { label: '会话与消息', path: '/conversations', icon: MessagesSquare },
  { label: 'Agent 管理', path: '/agents', icon: Bot },
  { label: '人格版本', path: '/personas', icon: Sparkles },
  { label: '模型与路由', path: '/models', icon: BrainCircuit },
  { label: 'Prompt 与上下文', path: '/prompts', icon: SlidersHorizontal },
  { label: '记忆与关系', path: '/memories', icon: MemoryStick },
]

const operationsNavigation: NavigationItem[] = [
  { label: '用户与身份', path: '/users', icon: Users },
  { label: '访问控制', path: '/access', icon: LockKeyhole },
  { label: '工具与策略', path: '/tools', icon: Wrench },
  { label: '渠道与适配器', path: '/channels', icon: Cable },
  { label: '外部身份与 Inbox', path: '/integrations', icon: Fingerprint },
  { label: '任务与主动行为', path: '/tasks', icon: ListChecks },
  { label: '评测实验室', path: '/evaluations', icon: FlaskConical },
  { label: '可观测性', path: '/observability', icon: Activity },
  { label: '审计日志', path: '/audit', icon: ScrollText },
  { label: '数据生命周期', path: '/data-lifecycle', icon: DatabaseBackup },
]

function NavigationGroup({
  title,
  items,
  kind,
}: {
  title: string
  items: NavigationItem[]
  kind: 'primary' | 'operations'
}) {
  return (
    <section className={`nav-group ${kind}`}>
      <p className="nav-label">{title}</p>
      <nav aria-label={`${title}导航`}>
        {items.map(({ icon: Icon, label, path }) => (
          <Link
            key={path}
            to={path}
            className="nav-item"
            activeProps={{ className: 'nav-item active' }}
            aria-label={label}
            title={label}
          >
            <Icon size={17} strokeWidth={1.8} />
            <span>{label}</span>
          </Link>
        ))}
      </nav>
    </section>
  )
}

export function AdminShell({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const selectedAgentId = useSelectedAgentId()
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const agents = useQuery({
    queryKey: ['managed-agents', 'selector'],
    queryFn: () => getManagedAgents('', 'active'),
    enabled: session.data?.permissions.includes('agent:read') ?? false,
  })
  const authentication = useAuthentication()
  const activeAgents = useMemo(() => agents.data?.items ?? [], [agents.data])

  useEffect(() => {
    if (!agents.data) return
    if (activeAgents.some((agent) => agent.id === selectedAgentId)) return
    setSelectedAgentId(activeAgents[0]?.id ?? null)
  }, [activeAgents, agents.data, selectedAgentId])

  const changeAgent = (agentId: string) => {
    setSelectedAgentId(agentId || null)
    void queryClient.invalidateQueries()
  }

  return (
    <div className="admin-shell">
      <a className="skip-link" href="#main-content">跳转到主要内容</a>
      <aside className="sidebar" aria-label="主导航">
        <div className="brand">
          <div className="brand-mark"><BrainCircuit size={21} /></div>
          <div>
            <strong>赛博网友</strong>
            <span>控制平面</span>
          </div>
        </div>

        <div className="environment-switcher">
          <span className="status-dot" />
          <div>
            <strong>本地开发环境</strong>
            <small>开发模式</small>
          </div>
          <ChevronDown size={15} />
        </div>

        <NavigationGroup title="心智系统" items={primaryNavigation} kind="primary" />
        <NavigationGroup title="运营与治理" items={operationsNavigation} kind="operations" />

        <div className="sidebar-footer">
          <Link
            to="/configuration"
            className="nav-item"
            activeProps={{ className: 'nav-item active' }}
          >
            <SlidersHorizontal size={17} />
            <span>配置中心</span>
          </Link>
          <Link
            to="/settings"
            className="nav-item"
            activeProps={{ className: 'nav-item active' }}
          >
            <Settings2 size={17} />
            <span>系统设置</span>
          </Link>
        </div>
      </aside>

      <main className="main-area" id="main-content" tabIndex={-1}>
        <header className="topbar">
          <div className="breadcrumb">
            <span>管理后台</span>
            <span>/</span>
            <strong>赛博网友核心</strong>
          </div>
          <div className="topbar-actions">
            <label className="agent-switcher">
              <Bot size={14} />
              <span>当前 Agent</span>
              <select
                aria-label="当前 Agent"
                value={selectedAgentId ?? ''}
                disabled={agents.isLoading || activeAgents.length === 0}
                onChange={(event) => changeAgent(event.target.value)}
              >
                {activeAgents.length === 0 && <option value="">暂无启用 Agent</option>}
                {activeAgents.map((agent) => (
                  <option key={agent.id} value={agent.id}>{agent.name}</option>
                ))}
              </select>
            </label>
            <div className="secure-badge">
              <ShieldCheck size={14} /> {session.data ? adminRoleLabels[session.data.role] : '正在验证权限'}
            </div>
            <button className="icon-button" aria-label="凭证管理"><KeyRound size={17} /></button>
            {authentication.mode === 'oidc' && (
              <button
                className="icon-button"
                aria-label="退出登录"
                onClick={() => void authentication.signOut()}
              >
                <LogOut size={17} />
              </button>
            )}
            <div className="avatar" title={session.data?.display_name}>CN</div>
          </div>
        </header>
        <div className="page-scroll">{children}</div>
      </main>
    </div>
  )
}
