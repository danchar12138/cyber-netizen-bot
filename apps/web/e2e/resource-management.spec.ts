import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const blankAgentId = '55555555-5555-4555-8555-555555555555'
const copiedAgentId = '66666666-6666-4666-8666-666666666666'
const timestamp = '2026-09-09T08:00:00Z'

test('可以创建、复制、切换并批量停用 Agent', async ({ page }) => {
  let agentStatus: 'active' | 'disabled' = 'active'
  const agents = [{
    id: agentId,
    tenant_id: tenantId,
    name: '赛博网友',
    status: agentStatus,
    created_at: timestamp,
  }]
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
        items: agents.map((agent) => ({ ...agent, status: agent.id === agentId ? agentStatus : agent.status })),
        next_cursor: null,
      },
    })
  })
  await page.route('**/api/v1/administration/agents', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const command = route.request().postDataJSON() as { name: string }
    expect(command.name).toBe('安静伙伴')
    const created = {
      id: blankAgentId,
      tenant_id: tenantId,
      name: command.name,
      status: 'active' as const,
      created_at: timestamp,
    }
    agents.push(created)
    await route.fulfill({ status: 201, json: created })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/copy`, async (route) => {
    const command = route.request().postDataJSON() as { name: string }
    expect(command.name).toBe('赛博网友副本')
    const copied = {
      id: copiedAgentId,
      tenant_id: tenantId,
      name: command.name,
      status: 'active' as const,
      created_at: timestamp,
    }
    agents.push(copied)
    await route.fulfill({ status: 201, json: copied })
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
  await page.getByLabel('新 Agent 名称').fill('安静伙伴')
  await page.getByRole('button', { name: '创建并切换' }).click()
  await expect(page.getByLabel('当前 Agent')).toHaveValue(blankAgentId)
  await expect(page.getByRole('table').getByText('安静伙伴', { exact: true })).toBeVisible()

  await page.getByLabel('源 Agent').selectOption(agentId)
  await page.getByLabel('复制后的 Agent 名称').fill('赛博网友副本')
  await page.getByRole('button', { name: '复制并切换' }).click()
  await expect(page.getByLabel('当前 Agent')).toHaveValue(copiedAgentId)
  await expect(page.getByRole('table').getByText('赛博网友副本', { exact: true })).toBeVisible()

  await page.getByRole('checkbox', { name: `选择 赛博网友 ${agentId} active` }).check()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '批量停用' }).click()
  await expect(page.getByText('已停用')).toBeVisible()

  await page.goto('/audit')
  await expect(page.getByText('更新 Agent 状态')).toBeVisible()
  await expect(page.getByText(/状态：已停用/)).toBeVisible()
})
