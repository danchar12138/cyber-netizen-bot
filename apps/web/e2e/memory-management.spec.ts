import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const memoryId = '55555555-5555-4555-8555-555555555555'
const sourceId = '66666666-6666-4666-8666-666666666666'
const timestamp = '2026-09-10T08:00:00Z'

function memory(content: string | null, confirmation = 'unconfirmed', status = 'active') {
  return {
    id: memoryId,
    lineage_id: memoryId,
    tenant_id: tenantId,
    agent_id: agentId,
    user_id: userId,
    conversation_id: null,
    episode_id: null,
    kind: 'semantic',
    visibility: 'user',
    content,
    event_at: timestamp,
    confidence: confirmation === 'confirmed' ? 0.9 : 0.7,
    importance: 0.5,
    emotional_weight: 0,
    sensitivity: 'normal',
    confirmation,
    status,
    version: 1,
    embedding_version: status === 'forgotten' ? null : 'local-hash-v1',
    created_by: userId,
    created_at: timestamp,
    updated_at: timestamp,
  }
}

async function mockIdentityAndSession(page: Page) {
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['memory:read', 'memory:write', 'memory:rebuild'],
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
}

test('可以创建、确认和遗忘带来源的长期记忆', async ({ page }) => {
  await mockIdentityAndSession(page)
  let currentMemory = memory('用户喜欢夜间散步')
  let sourceExcerpt: string | null = null

  await page.route('**/api/v1/memory/relationship?*', (route) =>
    route.fulfill({ status: 404, json: { error: { message: '尚无关系记录' } } }))
  await page.route('**/api/v1/memory/episodes?*', (route) =>
    route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v1/memory/index-jobs', (route) =>
    route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v1/memory/memories?*', (route) =>
    route.fulfill({ json: { items: [currentMemory] } }))
  await page.route(`**/api/v1/memory/memories/${memoryId}`, async (route) => {
    await route.fulfill({
      json: {
        memory: currentMemory,
        sources: [{
          id: sourceId,
          tenant_id: tenantId,
          memory_id: memoryId,
          kind: 'import',
          source_id: 'admin:test',
          excerpt: sourceExcerpt,
          is_verbatim: false,
          occurred_at: timestamp,
          created_at: timestamp,
        }],
        links: [],
      },
    })
  })
  await page.route(`**/api/v1/memory/memories/${memoryId}/confirmation`, async (route) => {
    currentMemory = memory('用户喜欢夜间散步', 'confirmed')
    await route.fulfill({ json: currentMemory })
  })
  await page.route(`**/api/v1/memory/memories/${memoryId}/forget`, async (route) => {
    currentMemory = memory(null, 'confirmed', 'forgotten')
    sourceExcerpt = null
    await route.fulfill({ json: currentMemory })
  })
  await page.route('**/api/v1/memory/memories', async (route) => {
    const command = route.request().postDataJSON() as { content: string; sources: object[] }
    expect(command.content).toBe('用户喜欢夜间散步')
    expect(command.sources).toHaveLength(1)
    await route.fulfill({
      status: 201,
      json: { memory: currentMemory, sources: [], links: [] },
    })
  })

  await page.goto('/memories')
  await expect(page.getByRole('heading', { name: '记忆与关系' })).toBeVisible()
  await page.getByRole('button', { name: '新建记忆' }).click()
  await page.getByLabel('记忆内容').fill('用户喜欢夜间散步')
  await page.getByRole('button', { name: '保存记忆' }).click()
  await expect(page.getByText('用户喜欢夜间散步').first()).toBeVisible()

  await page.getByRole('button', { name: /语义.*未确认/ }).click()
  await expect(page.getByText('非逐字摘要')).toBeVisible()
  await page.getByRole('button', { name: '确认', exact: true }).click()
  await expect(page.getByText('已确认', { exact: false }).first()).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '遗忘', exact: true }).click()
  await expect(page.getByText('正文已清除')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})

test('只读角色不能看到记忆写操作入口', async ({ page }) => {
  await mockIdentityAndSession(page)
  await page.route('**/api/v1/administration/session', (route) => route.fulfill({
    json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '只读用户',
      role: 'viewer',
      permissions: ['memory:read'],
      authentication_mode: 'development',
    },
  }))
  await page.route('**/api/v1/memory/memories?*', (route) =>
    route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v1/memory/relationship?*', (route) => route.fulfill({ status: 404 }))
  await page.route('**/api/v1/memory/episodes?*', (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/v1/memory/index-jobs', (route) => route.fulfill({ json: { items: [] } }))

  await page.goto('/memories')
  await expect(page.getByRole('button', { name: '新建记忆' })).toBeDisabled()
  await page.getByRole('button', { name: '索引任务' }).click()
  await expect(page.getByRole('button', { name: '重建全部' })).toBeDisabled()
})
