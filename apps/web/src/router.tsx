import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from '@tanstack/react-router'

import { AdminShell } from './shell/AdminShell'
import { AccessControlPage } from './views/AccessControlPage'
import { ChatPage } from './views/ChatPage'
import { ConfigurationPage } from './views/ConfigurationPage'
import { DashboardPage } from './views/DashboardPage'
import { SectionRoutePage } from './views/SectionRoutePage'

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

const accessControlRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/access',
  component: AccessControlPage,
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
  accessControlRoute,
  sectionRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
