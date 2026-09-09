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
import { ConfigurationPage } from './views/ConfigurationPage'
import { ConversationsPage } from './views/ConversationsPage'
import { DashboardPage } from './views/DashboardPage'
import { EntityManagementPage } from './views/EntityManagementPage'
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
  usersRoute,
  auditRoute,
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
