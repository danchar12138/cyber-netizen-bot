import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const timestamp = '2026-09-09T08:00:00Z'

test('一个标签页修改智能体后其他标签页自动刷新', async ({ context }) => {
  let agentStatus: 'active' | 'disabled' = 'active'
  await context.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['agent:read', 'agent:write'],
        authentication_mode: 'development',
      },
    })
  })
  await context.route('**/api/v1/administration/agents?*', async (route) => {
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
  await context.route('**/api/v1/administration/agents/status', async (route) => {
    agentStatus = 'disabled'
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })

  const firstPage = await context.newPage()
  const secondPage = await context.newPage()
  await Promise.all([firstPage.goto('/agents'), secondPage.goto('/agents')])
  await expect(firstPage.getByText('已启用')).toBeVisible()
  await expect(secondPage.getByText('已启用')).toBeVisible()

  await firstPage.getByRole('checkbox', { name: /选择 赛博网友/ }).check()
  firstPage.once('dialog', (dialog) => dialog.accept())
  await firstPage.getByRole('button', { name: '批量停用' }).click()

  await expect(firstPage.getByText('已停用')).toBeVisible()
  await expect(secondPage.getByText('已停用')).toBeVisible()
})

test('手机宽度可以打开会话列表并保持页面无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['conversation:read', 'conversation:use'],
        authentication_mode: 'development',
      },
    })
  })
  await page.route('**/api/v1/chat/identity', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        agent_id: agentId,
        user_name: '本地开发者',
        agent_name: '赛博网友',
      },
    })
  })
  await page.route('**/api/v1/chat/conversations?*', async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })

  await page.goto('/chat')
  const toggle = page.getByRole('button', { name: '显示会话列表' })
  await expect(toggle).toBeVisible()
  await toggle.click()
  await expect(page.getByRole('complementary', { name: '会话列表' })).toBeVisible()
  await expect(page.getByRole('button', { name: '隐藏会话列表' })).toBeVisible()
  expect(await page.evaluate<boolean>('document.documentElement.scrollWidth > window.innerWidth')).toBe(false)
})
