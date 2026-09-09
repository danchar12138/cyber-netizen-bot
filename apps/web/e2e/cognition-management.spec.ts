import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const draftId = '55555555-5555-4555-8555-555555555555'
const historicalId = '66666666-6666-4666-8666-666666666666'
const runId = '77777777-7777-4777-8777-777777777777'
const timestamp = '2026-09-10T08:00:00Z'

async function mockAdminSession(page: Page) {
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['cognition:read', 'cognition:write', 'cognition:evaluate', 'trace:read'],
        authentication_mode: 'development',
      },
    })
  })
}

function personaResource(id: string, version: number, status: 'draft' | 'published' | 'superseded') {
  return {
    id,
    tenant_id: tenantId,
    agent_id: agentId,
    kind: 'persona',
    key: 'default',
    name: '默认赛博网友',
    version,
    status,
    payload: {
      identity: '自然、诚实的赛博网友',
      purpose: '长期交流',
      principles: ['不捏造事实'],
      boundaries: ['尊重用户边界'],
      traits: { warmth: 0.8, curiosity: 0.7, humor: 0.4, directness: 0.6, initiative: 0.5 },
      style: { address_style: '自然', sentence_length: '短句', emoji_frequency: '少量' },
    },
    note: null,
    created_by: userId,
    created_at: timestamp,
    published_at: status === 'draft' ? null : timestamp,
  }
}

test('可以测试、保存、发布和回滚人格版本', async ({ page }) => {
  await mockAdminSession(page)
  let resources = [personaResource(historicalId, 1, 'superseded')]
  await page.route('**/api/v1/cognition/resources?kind=persona', async (route) => {
    await route.fulfill({ json: { items: resources } })
  })
  await page.route('**/api/v1/cognition/resources/test', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ kind: 'persona' })
    await route.fulfill({ json: { valid: true, messages: ['结构、类型和安全边界校验通过。'] } })
  })
  await page.route('**/api/v1/cognition/resources', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      kind: 'persona',
      key: 'default',
      name: '默认赛博网友',
    })
    const draft = personaResource(draftId, 2, 'draft')
    resources = [draft, ...resources]
    await route.fulfill({ status: 201, json: draft })
  })
  await page.route(`**/api/v1/cognition/resources/${draftId}/publish`, async (route) => {
    resources = resources.map((resource) => (
      resource.id === draftId ? personaResource(draftId, 2, 'published') : resource
    ))
    await route.fulfill({ json: resources[0] })
  })
  await page.route(`**/api/v1/cognition/resources/${historicalId}/rollback`, async (route) => {
    const rolledBack = personaResource('88888888-8888-4888-8888-888888888888', 3, 'published')
    resources = [rolledBack, ...resources.map((resource) => ({ ...resource, status: 'superseded' as const }))]
    await route.fulfill({ json: rolledBack })
  })

  await page.goto('/personas')
  await page.getByRole('button', { name: '新建草稿' }).click()
  await page.getByRole('button', { name: '测试', exact: true }).click()
  await expect(page.getByText('结构、类型和安全边界校验通过。')).toBeVisible()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page.getByText('草稿', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '发布', exact: true }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '回滚为新版本' }).first().click()
  await expect(page.getByText('default · v3')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})

test('可以运行拟人边界回放并查看通过率', async ({ page }) => {
  await mockAdminSession(page)
  await page.route('**/api/v1/cognition/evaluations/run', async (route) => {
    await route.fulfill({
      json: {
        passed: 5,
        total: 5,
        cases: [{
          case_id: 'natural-question',
          category: '自然度',
          input_text: '周末不知道做什么，你说呢？',
          expected_action: 'reply',
          actual_action: 'reply',
          passed: true,
          summary: '行动符合预期。',
        }],
      },
    })
  })

  await page.goto('/evaluations')
  await page.getByRole('button', { name: '运行内置回放集' }).click()
  await expect(page.getByText('5 / 5 通过')).toBeVisible()
  await expect(page.getByText('自然度', { exact: true })).toBeVisible()
})

test('可以按 Run ID 查看安全认知轨迹', async ({ page }) => {
  await mockAdminSession(page)
  await page.route(`**/api/v1/cognition/runs/${runId}/trace`, async (route) => {
    await route.fulfill({
      json: {
        run_id: runId,
        persona_state: {
          persona_version: 2,
          valence: 0.25,
          arousal: 0.4,
          social_energy: 0.7,
          created_at: timestamp,
        },
        steps: ['perception', 'context_assembly', 'social_mind', 'deliberation', 'policy_gate', 'realizer'].map((stage, index) => ({
          sequence: index + 1,
          stage,
          summary: `${stage} 安全摘要`,
          detail: { duration_ms: index + 1 },
          created_at: timestamp,
        })),
        candidates: [{
          sequence: 1,
          action: 'reply',
          confidence: 0.92,
          reason_summary: '自然回应当前问题。',
          parameters: {},
          tool_name: null,
          risk_level: 'none',
          selected: true,
          rejection_reason: null,
        }],
        model_invocations: [{
          purpose: 'chat.realizer',
          provider: 'development',
          model: 'friendly-echo-v1',
          attempt: 1,
          status: 'completed',
          input_tokens: 120,
          output_tokens: 32,
          latency_ms: 18,
          error_code: null,
          created_at: timestamp,
          completed_at: timestamp,
        }],
      },
    })
  })

  await page.goto('/observability')
  await page.getByRole('textbox', { name: 'Agent Run ID' }).fill(runId)
  await page.getByRole('button', { name: '查询轨迹' }).click()
  await expect(page.getByText('6 步')).toBeVisible()
  await expect(page.getByText('策略已选')).toBeVisible()
  await expect(page.getByText('development / friendly-echo-v1')).toBeVisible()
  await expect(page.getByText('隐藏思维链或请求正文')).toBeVisible()
})
