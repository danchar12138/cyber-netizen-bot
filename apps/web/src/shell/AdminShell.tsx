import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  Bot,
  BrainCircuit,
  Cable,
  ChevronDown,
  CircleGauge,
  FlaskConical,
  KeyRound,
  LockKeyhole,
  ListChecks,
  MemoryStick,
  MessageCircleMore,
  ScrollText,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Users,
  Wrench,
} from 'lucide-react'
import type { ComponentType, ReactNode } from 'react'

import { getAdminSession } from '../api'

interface NavigationItem {
  label: string
  path: string
  icon: ComponentType<{ size?: number; strokeWidth?: number }>
}

const primaryNavigation: NavigationItem[] = [
  { label: '运行总览', path: '/', icon: CircleGauge },
  { label: '内部对话', path: '/chat', icon: MessageCircleMore },
  { label: 'Agent 与人格', path: '/agents', icon: Bot },
  { label: '模型与路由', path: '/models', icon: BrainCircuit },
  { label: 'Prompt 与上下文', path: '/prompts', icon: Sparkles },
  { label: '记忆与关系', path: '/memories', icon: MemoryStick },
]

const operationsNavigation: NavigationItem[] = [
  { label: '用户与身份', path: '/users', icon: Users },
  { label: '访问控制', path: '/access', icon: LockKeyhole },
  { label: '工具与策略', path: '/tools', icon: Wrench },
  { label: '渠道与适配器', path: '/channels', icon: Cable },
  { label: '任务与主动行为', path: '/tasks', icon: ListChecks },
  { label: '评测实验室', path: '/evaluations', icon: FlaskConical },
  { label: '运行轨迹', path: '/observability', icon: Activity },
  { label: '审计日志', path: '/audit', icon: ScrollText },
]

function NavigationGroup({ title, items }: { title: string; items: NavigationItem[] }) {
  return (
    <section className="nav-group">
      <p className="nav-label">{title}</p>
      <nav>
        {items.map(({ icon: Icon, label, path }) => (
          <Link
            key={path}
            to={path}
            className="nav-item"
            activeProps={{ className: 'nav-item active' }}
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
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const roleLabels = { admin: '管理员', operator: '运营者', viewer: '只读' }

  return (
    <div className="admin-shell">
      <aside className="sidebar">
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
            <small>development</small>
          </div>
          <ChevronDown size={15} />
        </div>

        <NavigationGroup title="心智系统" items={primaryNavigation} />
        <NavigationGroup title="运营与治理" items={operationsNavigation} />

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
            to="/$section"
            params={{ section: 'settings' }}
            className="nav-item"
            activeProps={{ className: 'nav-item active' }}
          >
            <Settings2 size={17} />
            <span>系统设置</span>
          </Link>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb">
            <span>管理后台</span>
            <span>/</span>
            <strong>赛博网友核心</strong>
          </div>
          <div className="topbar-actions">
            <div className="secure-badge">
              <ShieldCheck size={14} /> {session.data ? roleLabels[session.data.role] : '正在验证权限'}
            </div>
            <button className="icon-button" aria-label="凭证管理"><KeyRound size={17} /></button>
            <div className="avatar" title={session.data?.display_name}>CN</div>
          </div>
        </header>
        <div className="page-scroll">{children}</div>
      </main>
    </div>
  )
}
