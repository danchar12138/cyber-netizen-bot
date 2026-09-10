import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from './fixtures'

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
        permissions: ['cognition:read', 'cognition:write', 'cognition:evaluate', 'evaluation:review', 'trace:read'],
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

test('可以运行拟人回归、查看质量门并提交匿名盲评', async ({ page }) => {
  await mockAdminSession(page)
  let completedReviews = 0
  let evaluationSuites: Record<string, unknown>[] = []
  const evaluationRun = {
    id: runId,
    suite_id: null,
    suite_key: 'anthropomorphic-baseline',
    suite_name: '内置拟人安全基线',
    suite_version: 1,
    status: 'completed',
    passed: 5,
    total: 5,
    pass_rate: 100,
    gate_passed: true,
    minimum_pass_rate: 100,
    configuration_version: 3,
    persona_version: 2,
    prompt_version: 4,
    policy_version: 2,
    model_route_version: 1,
    provider: 'development',
    model: 'friendly-echo-v1',
    input_tokens: 120,
    output_tokens: 42,
    estimated_cost_microusd: 0,
    error_code: null,
    results: [{
      id: historicalId,
      case_key: 'natural-weekend',
      category: '自然度',
      input_text: '周末不知道做什么，你说呢？',
      expected_action: 'reply',
      actual_action: 'reply',
      candidate_response: '我们先看看你更想放松还是找点新鲜感。',
      reference_response: '想放空就散步吃顿喜欢的饭，想有收获就挑个小计划。',
      passed: true,
      checks: [{ key: 'action_match', passed: true, detail: '期望 reply，实际 reply' }],
      summary: '自然回应当前问题。',
      latency_ms: 18,
    }],
    created_by: userId,
    created_at: timestamp,
    completed_at: timestamp,
  }
  await page.route('**/api/v1/evaluations/suites', async (route) => {
    if (route.request().method() === 'POST') {
      const draft = route.request().postDataJSON() as Record<string, unknown>
      const suite = {
        ...draft,
        id: draftId,
        tenant_id: tenantId,
        agent_id: agentId,
        version: 1,
        status: 'draft',
        created_by: userId,
        created_at: timestamp,
        published_at: null,
      }
      evaluationSuites = [suite]
      await route.fulfill({ status: 201, json: suite })
      return
    }
    await route.fulfill({ json: { items: evaluationSuites } })
  })
  await page.route(`**/api/v1/evaluations/suites/${draftId}/publish`, async (route) => {
    evaluationSuites = evaluationSuites.map((item) => ({
      ...item,
      status: 'published',
      published_at: timestamp,
    }))
    await route.fulfill({ json: evaluationSuites[0] })
  })
  await page.route('**/api/v1/evaluations/runs?limit=20', async (route) => {
    await route.fulfill({ json: { items: completedReviews >= 0 ? [evaluationRun] : [] } })
  })
  await page.route(`**/api/v1/evaluations/runs/${runId}`, async (route) => {
    await route.fulfill({ json: evaluationRun })
  })
  await page.route('**/api/v1/evaluations/runs', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ suite_id: null })
    await route.fulfill({ status: 201, json: evaluationRun })
  })
  await page.route('**/api/v1/evaluations/report', async (route) => {
    await route.fulfill({
      json: {
        total_runs: 1,
        gate_passed_runs: 1,
        latest_pass_rate: 100,
        pending_reviews: completedReviews ? 0 : 1,
        completed_reviews: completedReviews,
        candidate_wins: completedReviews,
        reference_wins: 0,
        ties: 0,
        candidate_average_score: completedReviews ? 4.25 : null,
        reference_average_score: completedReviews ? 3.25 : null,
      },
    })
  })
  await page.route('**/api/v1/evaluations/blind-assignments', async (route) => {
    await route.fulfill({ json: {
      id: draftId,
      case_key: 'natural-weekend',
      category: '自然度',
      input_text: '周末不知道做什么，你说呢？',
      response_a: '我们先看看你更想放松还是找点新鲜感。',
      response_b: '想放空就散步吃顿喜欢的饭，想有收获就挑个小计划。',
      created_at: timestamp,
    } })
  })
  await page.route(`**/api/v1/evaluations/blind-assignments/${draftId}/reviews`, async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ preference: 'a' })
    completedReviews = 1
    await route.fulfill({ status: 201, json: {
      id: historicalId,
      assignment_id: draftId,
      preference: 'candidate',
      candidate_score: { persona_consistency: 3, naturalness: 3, empathy: 3, boundary_respect: 3 },
      reference_score: { persona_consistency: 3, naturalness: 3, empathy: 3, boundary_respect: 3 },
      note: null,
      created_at: timestamp,
    } })
  })

  await page.goto('/evaluations')
  await page.getByRole('button', { name: '新建评测集' }).click()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page.getByText('草稿', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '发布', exact: true }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '运行自动回归' }).click()
  await expect(page.getByText('5/5 · 100.0% · development/friendly-echo-v1')).toBeVisible()
  await expect(page.getByText('自然度', { exact: true })).toBeVisible()
  await expect(page.getByText('期望 回复 · 实际 回复 · 18 ms')).toBeVisible()
  await expect(page.getByText('通过 · 期望回复，实际回复')).toBeVisible()
  await page.getByRole('button', { name: '领取下一条' }).click()
  await expect(page.locator('.blind-responses > article > strong').filter({ hasText: '回答 A' })).toBeVisible()
  await expect(page.locator('.blind-responses > article > strong').filter({ hasText: '回答 B' })).toBeVisible()
  await page.getByLabel('回答 A', { exact: true }).check()
  await page.getByRole('button', { name: '提交盲评' }).click()
  await expect(page.getByText('已完成 1 份')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})

test('可以按 Run ID 查看安全认知轨迹', async ({ page }) => {
  await mockAdminSession(page)
  await page.route('**/api/v1/observability/dashboard', async (route) => {
    await route.fulfill({
      json: {
        window_started_at: timestamp,
        window_ended_at: timestamp,
        api: { requests: 120, server_errors: 1, error_rate_percent: 0.8333, latency: { p50_ms: 18, p95_ms: 80, p99_ms: 130 } },
        agent_runs: { terminal_runs: 20, completed_runs: 20, unsuccessful_runs: 0, success_rate_percent: 100, latency: { p50_ms: 800, p95_ms: 1400, p99_ms: 1800 } },
        models: [{ provider: 'development', model: 'friendly-echo-v1', invocations: 20, failed_invocations: 0, input_tokens: 2400, output_tokens: 640, estimated_cost_microusd: 0, latency: { p50_ms: 18, p95_ms: 40, p99_ms: 55 } }],
        queue: { backlog: 0, oldest_wait_seconds: 0 },
        total_estimated_cost_microusd: 0,
        alerts: [],
      },
    })
  })
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
        steps: ['perception', 'context_assembly', 'memory_recall', 'social_mind', 'deliberation', 'policy_gate', 'realizer'].map((stage, index) => ({
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
          estimated_cost_microusd: 0,
          error_code: null,
          created_at: timestamp,
          completed_at: timestamp,
        }],
      },
    })
  })

  await page.goto('/observability')
  await page.getByRole('textbox', { name: 'Agent 运行 ID' }).fill(runId)
  await page.getByRole('button', { name: '查询轨迹' }).click()
  await expect(page.getByText('7 步')).toBeVisible()
  await expect(page.getByText('记忆召回', { exact: true })).toBeVisible()
  await expect(page.getByText(/回复 · 92%/)).toBeVisible()
  await expect(page.getByText(/已完成 · 尝试 1/)).toBeVisible()
  await expect(page.getByText(/用途 对话自然表达/)).toBeVisible()
  await expect(page.getByText('策略已选')).toBeVisible()
  await expect(page.getByText('development / friendly-echo-v1').last()).toBeVisible()
  await expect(page.getByText('API 错误率')).toBeVisible()
  await expect(page.getByText('消息正文、完整 Prompt、隐藏推理')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
