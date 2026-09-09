import { expect, test } from '@playwright/test'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const timestamp = '2026-09-09T08:00:00Z'

test('可以批量停用 Agent 并查看审计结果', async ({ page }) => {
  let agentStatus: 'active' | 'disabled' = 'active'
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['agent:read', 'agent:write', 'audit:read'],
        authentication_mode: 'development',
      },
    })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    await route.fulfill({
      json: {
        items: [{
          id: agentId,
          tenant_id: tenantId,
          name: '赛博网友',
          status: agentStatus,
          created_at: timestamp,
        }],
        next_cursor: null,
      },
    })
  })
  await page.route('**/api/v1/administration/agents/status', async (route) => {
    const command = route.request().postDataJSON() as {
      ids: string[]
      status: 'active' | 'disabled'
      confirmed: boolean
    }
    expect(command).toEqual({ ids: [agentId], status: 'disabled', confirmed: true })
    agentStatus = command.status
    await route.fulfill({
      json: {
        items: [{
          id: agentId,
          tenant_id: tenantId,
          name: '赛博网友',
          status: agentStatus,
          created_at: timestamp,
        }],
        next_cursor: null,
      },
    })
  })
  await page.route('**/api/v1/administration/audit?*', async (route) => {
    await route.fulfill({
      json: {
        items: [{
          id: 1,
          actor_id: userId,
          action: 'agent.status_updated',
          resource_type: 'agent',
          resource_id: null,
          detail: { ids: [agentId], status: 'disabled' },
          created_at: timestamp,
        }],
        next_cursor: null,
      },
    })
  })

  await page.goto('/agents')
  await page.getByRole('checkbox', { name: /选择 赛博网友/ }).check()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '批量停用' }).click()
  await expect(page.getByText('已停用')).toBeVisible()

  await page.goto('/audit')
  await expect(page.getByText('agent.status_updated')).toBeVisible()
  await expect(page.getByText(/disabled/)).toBeVisible()
})
