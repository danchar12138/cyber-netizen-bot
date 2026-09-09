import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from '@tanstack/react-router'

import { AdminShell } from './shell/AdminShell'
import { AccessControlPage } from './views/AccessControlPage'
import { AuditLogPage } from './views/AuditLogPage'
import { ChatPage } from './views/ChatPage'
import { ChannelsPage } from './views/ChannelsPage'
import { ConfigurationPage } from './views/ConfigurationPage'
import { ConversationsPage } from './views/ConversationsPage'
import { CognitionResourcesPage } from './views/CognitionResourcesPage'
import { DashboardPage } from './views/DashboardPage'
import { EntityManagementPage } from './views/EntityManagementPage'
import { EvaluationsPage } from './views/EvaluationsPage'
import { MemoryPage } from './views/MemoryPage'
import { ObservabilityPage } from './views/ObservabilityPage'
import { SectionRoutePage } from './views/SectionRoutePage'
import { SystemSettingsPage } from './views/SystemSettingsPage'
import { TaskStatusPage } from './views/TaskStatusPage'

const rootRoute = createRootRoute({
  component: () => (
    <AdminShell>
      <Outlet />
    </AdminShell>
  ),
})

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: DashboardPage,
})

const configurationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/configuration',
  component: ConfigurationPage,
})

const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/chat',
  component: ChatPage,
})

const conversationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/conversations',
  component: ConversationsPage,
})

const accessControlRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/access',
  component: AccessControlPage,
})

const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/agents',
  component: () => <EntityManagementPage kind="agents" />,
})

const personasRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/personas',
  component: () => <CognitionResourcesPage title="人格版本" kinds={['persona']} description="管理人格宪法、稳定特质、表达风格与关系边界，发布后由新 Agent Run 固定引用。" />,
})

const modelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/models',
  component: () => <CognitionResourcesPage title="模型与用途路由" kinds={['model_profile', 'model_route']} description="按用途管理模型能力档案、主路由、有限重试与降级链；密钥仍由配置中心安全保存。" />,
})

const promptsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/prompts',
  component: () => <CognitionResourcesPage title="Prompt 与上下文" kinds={['prompt']} description="版本化管理表达 Prompt；上下文选择由可回放的来源、优先级和 Token 预算控制。" />,
})

const memoriesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/memories',
  component: MemoryPage,
})

const toolsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/tools',
  component: () => <CognitionResourcesPage title="工具与策略" kinds={['tool', 'policy']} description="定义类型化工具能力与策略门；未经允许的风险、预算和外部副作用会在执行前被拒绝。" />,
})

const evaluationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/evaluations',
  component: EvaluationsPage,
})

const observabilityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/observability',
  component: ObservabilityPage,
})

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/users',
  component: () => <EntityManagementPage kind="users" />,
})

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/audit',
  component: AuditLogPage,
})

const tasksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/tasks',
  component: TaskStatusPage,
})

const channelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/channels',
  component: ChannelsPage,
})

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  component: SystemSettingsPage,
})

const sectionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/$section',
  component: SectionRoutePage,
})

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  configurationRoute,
  chatRoute,
  conversationsRoute,
  accessControlRoute,
  agentsRoute,
  personasRoute,
  modelsRoute,
  promptsRoute,
  memoriesRoute,
  usersRoute,
  auditRoute,
  toolsRoute,
  evaluationsRoute,
  observabilityRoute,
  channelsRoute,
  tasksRoute,
  settingsRoute,
  sectionRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
