import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const draftId = '55555555-5555-4555-8555-555555555555'
const historicalId = '66666666-6666-4666-8666-666666666666'
const runId = '77777777-7777-4777-8777-777777777777'
const comparisonId = '99999999-9999-4999-8999-999999999999'
const decisionId = '12121212-1212-4212-8212-121212121212'
const timestamp = '2026-09-10T08:00:00Z'

async function mockAdminSession(page: Page) {
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: [
          'cognition:read',
          'cognition:write',
          'cognition:evaluate',
          'evaluation:review',
          'trace:read',
          'data_lifecycle:export',
        ],
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
  let evaluationDecisions: Record<string, unknown>[] = []
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
  const qualityRun = {
    ...evaluationRun,
    id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    model: 'friendly-quality-v1',
    input_tokens: 135,
    output_tokens: 58,
    estimated_cost_microusd: 753,
    results: evaluationRun.results.map((result) => ({
      ...result,
      id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      candidate_response: '质量候选回答：先看你更想休息，还是找一点新鲜感。',
      latency_ms: 31,
    })),
  }
  const comparison = {
    id: comparisonId,
    suite_id: null,
    suite_key: 'anthropomorphic-baseline',
    suite_name: '内置拟人安全基线',
    suite_version: 1,
    status: 'completed',
    configuration_version: 3,
    persona_version: 2,
    prompt_version: 4,
    policy_version: 2,
    model_route_version: 1,
    entries: [
      {
        position: 1,
        profile_key: 'fast',
        profile_version: 1,
        run: {
          ...evaluationRun,
          results: evaluationRun.results.map((result) => ({
            ...result,
            candidate_response: '速度候选回答：我们先看看你更想放松还是找点新鲜感。',
          })),
        },
      },
      { position: 2, profile_key: 'quality', profile_version: 1, run: qualityRun },
    ],
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
  await page.route('**/api/v1/evaluations/comparison-targets', async (route) => {
    await route.fulfill({ json: { items: [
      { profile_key: 'fast', profile_version: 1, provider: 'development', model: 'friendly-fast-v1' },
      { profile_key: 'quality', profile_version: 1, provider: 'development', model: 'friendly-quality-v1' },
    ] } })
  })
  await page.route('**/api/v1/evaluations/comparisons?limit=20', async (route) => {
    await route.fulfill({ json: { items: [{
      ...comparison,
      entries: comparison.entries.map((entry) => ({
        ...entry,
        run: {
          id: entry.run.id,
          suite_name: entry.run.suite_name,
          suite_version: entry.run.suite_version,
          status: entry.run.status,
          passed: entry.run.passed,
          total: entry.run.total,
          pass_rate: entry.run.pass_rate,
          gate_passed: entry.run.gate_passed,
          provider: entry.run.provider,
          model: entry.run.model,
          created_at: entry.run.created_at,
        },
      })),
    }] } })
  })
  await page.route(`**/api/v1/evaluations/comparisons/${comparisonId}`, async (route) => {
    await route.fulfill({ json: comparison })
  })
  await page.route('**/api/v1/evaluations/comparisons', async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      profile_keys: ['fast', 'quality'],
      suite_id: null,
    })
    await route.fulfill({ status: 201, json: comparison })
  })
  await page.route('**/api/v1/evaluations/quality-overview?*', async (route) => {
    await route.fulfill({
      json: {
        generated_at: timestamp,
        evaluation_scope: 'current_agent_all_history',
        operations_window_minutes: 10080,
        coverage: 'complete',
        evaluation: {
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
        alert_recommendations: {
          window_started_at: '2026-09-03T08:00:00Z',
          window_ended_at: timestamp,
          total: 4,
          accepted: 3,
          rejected: 1,
          acceptance_rate_percent: 75,
          accepted_resolved: 2,
          accepted_active: 1,
          replay_total: 3,
          replay_allowed: 2,
          replay_blocked: 1,
          actions: [],
          sources: [],
        },
        automatic_actions_allowed: false,
      },
    })
  })
  await page.route('**/api/v1/evaluations/quality-history?*', async (route) => {
    const url = new URL(route.request().url())
    const windowMinutes = Number(url.searchParams.get('window_minutes'))
    const bucketMinutes = Number(url.searchParams.get('bucket_minutes'))
    const days = windowMinutes / 1_440
    expect([7, 30, 90]).toContain(days)
    expect(bucketMinutes).toBe(1_440)
    const windowEndedAt = new Date(timestamp)
    const windowStartedAt = new Date(windowEndedAt.getTime() - windowMinutes * 60_000)
    const trend = Array.from({ length: days }, (_, index) => {
      const bucketStartedAt = new Date(windowStartedAt.getTime() + index * bucketMinutes * 60_000)
      const bucketEndedAt = new Date(bucketStartedAt.getTime() + bucketMinutes * 60_000)
      const hasRun = index >= days - 2
      return {
        bucket_started_at: bucketStartedAt.toISOString(),
        bucket_ended_at: bucketEndedAt.toISOString(),
        total_runs: hasRun ? 5 : 0,
        gate_passed_runs: index === days - 2 ? 4 : index === days - 1 ? 5 : 0,
        average_pass_rate: index === days - 2 ? 80 : index === days - 1 ? 100 : null,
        completed_reviews: hasRun ? 5 : 0,
        candidate_wins: hasRun ? 4 : 0,
        reference_wins: 0,
        ties: hasRun ? 1 : 0,
        candidate_average_score: index === days - 2 ? 3.25 : index === days - 1 ? 4.25 : null,
        reference_average_score: index === days - 2 ? 3.5 : index === days - 1 ? 3.25 : null,
      }
    })
    const baselineSnapshot = {
      suite_key: 'anthropomorphic-baseline',
      suite_version: 1,
      configuration_version: 3,
      persona_version: 2,
      prompt_version: 4,
      policy_version: 2,
      model_route_version: 1,
      provider: 'development',
      model: 'friendly-quality-v1',
    }
    const candidateSnapshot = {
      ...baselineSnapshot,
      configuration_version: 4,
      persona_version: 3,
    }
    const baselineVersion = {
      snapshot: baselineSnapshot,
      first_run_at: '2026-09-09T07:55:00Z',
      latest_run_at: '2026-09-09T08:00:00Z',
      total_runs: 5,
      gate_passed_runs: 4,
      average_pass_rate: 80,
      completed_reviews: 5,
      candidate_wins: 4,
      reference_wins: 0,
      ties: 1,
      candidate_average_score: 3.25,
      reference_average_score: 3.5,
    }
    const candidateVersion = {
      snapshot: candidateSnapshot,
      first_run_at: '2026-09-10T07:55:00Z',
      latest_run_at: timestamp,
      total_runs: 5,
      gate_passed_runs: 5,
      average_pass_rate: 100,
      completed_reviews: 5,
      candidate_wins: 4,
      reference_wins: 0,
      ties: 1,
      candidate_average_score: 4.25,
      reference_average_score: 3.25,
    }
    await route.fulfill({
      json: {
        generated_at: timestamp,
        window_started_at: windowStartedAt.toISOString(),
        window_ended_at: timestamp,
        window_minutes: windowMinutes,
        bucket_minutes: bucketMinutes,
        review_attribution: 'run_created_at',
        total_runs: 10,
        completed_reviews: 10,
        trend,
        versions: [candidateVersion, baselineVersion],
        baseline_comparisons: [
          {
            key: {
              suite_key: baselineSnapshot.suite_key,
              suite_version: baselineSnapshot.suite_version,
              provider: baselineSnapshot.provider,
              model: baselineSnapshot.model,
            },
            candidate: candidateVersion,
            baseline: baselineVersion,
            minimum_runs_per_snapshot: 5,
            minimum_reviews_per_snapshot: 5,
            automatic_regression_comparable: true,
            blind_review_comparable: true,
            pass_rate_delta_percentage_points: 20,
            candidate_average_score_delta: 1,
            reference_average_score_delta: -0.25,
            statistical_significance_assessed: false,
            causal_conclusion_allowed: false,
          },
        ],
        comparable_versions: true,
        automatic_actions_allowed: false,
      },
    })
  })
  await page.route(/\/api\/v1\/evaluations\/decisions(?:\/.*)?(?:\?.*)?$/, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/export')) {
      await route.fulfill({
        body: JSON.stringify({ schema_version: 1, id: decisionId }),
        contentType: 'application/json',
        headers: { 'X-Content-SHA256': 'b'.repeat(64) },
      })
      return
    }
    if (request.method() === 'POST') {
      const command = request.postDataJSON() as {
        window_minutes: number
        candidate: Record<string, unknown>
        baseline: Record<string, unknown>
        outcome: string
        reason: string
      }
      const versionSummary = (snapshot: Record<string, unknown>, candidate: boolean) => ({
        snapshot,
        first_run_at: candidate ? '2026-09-10T07:55:00Z' : '2026-09-09T07:55:00Z',
        latest_run_at: candidate ? timestamp : '2026-09-09T08:00:00Z',
        total_runs: 5,
        gate_passed_runs: candidate ? 5 : 4,
        average_pass_rate: candidate ? 100 : 80,
        completed_reviews: 5,
        candidate_wins: 4,
        reference_wins: 0,
        ties: 1,
        candidate_average_score: candidate ? 4.25 : 3.25,
        reference_average_score: candidate ? 3.25 : 3.5,
      })
      const decision = {
        id: decisionId,
        created_by: userId,
        created_at: timestamp,
        outcome: command.outcome,
        reason: command.reason,
        sha256: 'b'.repeat(64),
        report: {
          schema_version: 1,
          id: decisionId,
          tenant_id: tenantId,
          agent_id: agentId,
          created_by: userId,
          created_at: timestamp,
          window_started_at: '2026-08-11T08:00:00Z',
          window_ended_at: timestamp,
          window_minutes: command.window_minutes,
          review_attribution: 'run_created_at',
          comparison: {
            key: {
              suite_key: command.candidate.suite_key,
              suite_version: command.candidate.suite_version,
              provider: command.candidate.provider,
              model: command.candidate.model,
            },
            candidate: versionSummary(command.candidate, true),
            baseline: versionSummary(command.baseline, false),
            minimum_runs_per_snapshot: 5,
            minimum_reviews_per_snapshot: 5,
            automatic_regression_comparable: true,
            blind_review_comparable: true,
            pass_rate_delta_percentage_points: 20,
            candidate_average_score_delta: 1,
            reference_average_score_delta: -0.25,
            statistical_significance_assessed: false,
            causal_conclusion_allowed: false,
          },
          outcome: command.outcome,
          reason: command.reason,
          statistical_significance_assessed: false,
          causal_conclusion_allowed: false,
          automatic_actions_allowed: false,
        },
      }
      evaluationDecisions = [decision]
      await route.fulfill({ status: 201, json: decision })
      return
    }
    if (url.pathname.endsWith(`/${decisionId}`)) {
      await route.fulfill({ json: evaluationDecisions[0] })
      return
    }
    await route.fulfill({
      json: {
        items: evaluationDecisions.map((decision) => ({
          id: decision.id,
          created_by: decision.created_by,
          created_at: decision.created_at,
          outcome: decision.outcome,
          reason: decision.reason,
          sha256: decision.sha256,
        })),
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
  await expect(page.getByText('拟人：当前 Agent 全历史 · 告警：近 7 天')).toBeVisible()
  await expect(page.getByText('证据完整')).toBeVisible()
  await expect(page.getByText('75.0%')).toBeVisible()
  await expect(page.getByText('只读质量事实，不会自动调参、发布或处置告警')).toBeVisible()
  await expect(page.getByText('近 30 天 · 10 次回归 · 10 份盲评')).toBeVisible()
  await expect(page.locator('.quality-trend-bucket')).toHaveCount(30)
  await expect(page.locator('.quality-version-table').getByText('development/friendly-quality-v1').first()).toBeVisible()
  await expect(page.getByText('+20.00 个百分点')).toBeVisible()
  await expect(page.getByText('+1.00')).toBeVisible()
  await expect(page.getByText('-0.25')).toBeVisible()
  await expect(page.getByText('样本门槛只决定是否展示差值；本比较未进行统计显著性评估，也不允许作因果结论。')).toBeVisible()
  await expect(page.getByText('决策记录不会自动调参、发布配置或改变运行时行为')).toBeVisible()
  await page.getByRole('button', { name: '创建决策记录' }).click()
  await expect(page.getByLabel('评测决策历史').getByText('等待更多证据', { exact: true })).toBeVisible()
  await expect(page.getByText(`SHA-256`)).toBeVisible()
  await page.getByRole('button', { name: '下载 JSON' }).click()
  await expect(page.getByText(`报告已开始下载，SHA-256：${'b'.repeat(64)}。`)).toBeVisible()
  await page.getByRole('button', { name: '7 天' }).click()
  await expect(page.getByText('近 7 天 · 10 次回归 · 10 份盲评')).toBeVisible()
  await expect(page.locator('.quality-trend-bucket')).toHaveCount(7)
  await page.getByRole('button', { name: '90 天' }).click()
  await expect(page.getByText('近 90 天 · 10 次回归 · 10 份盲评')).toBeVisible()
  await expect(page.locator('.quality-trend-bucket')).toHaveCount(90)
  expect(await page.evaluate<boolean>('document.documentElement.scrollWidth > window.innerWidth')).toBe(false)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate<boolean>('document.documentElement.scrollWidth > window.innerWidth')).toBe(false)
  await expect.poll(() => page.evaluate<boolean>(
    "(() => { const element = document.querySelector('.quality-trend-scroll'); return element !== null && element.scrollWidth > element.clientWidth })()",
  )).toBe(true)
  await page.setViewportSize({ width: 1280, height: 720 })
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
  await page.getByLabel('fast · v1').check()
  await page.getByLabel('quality · v1').check()
  await page.getByRole('button', { name: '运行同源对比' }).click()
  await expect(page.getByText('速度候选回答：我们先看看你更想放松还是找点新鲜感。')).toBeVisible()
  await expect(page.getByText('质量候选回答：先看你更想休息，还是找一点新鲜感。')).toBeVisible()
  await expect(page.getByText('120 / 42')).toBeVisible()
  await expect(page.getByText('$0.000753')).toBeVisible()
  await page.getByRole('button', { name: '领取下一条' }).click()
  await expect(page.locator('.blind-responses > article > strong').filter({ hasText: '回答 A' })).toBeVisible()
  await expect(page.locator('.blind-responses > article > strong').filter({ hasText: '回答 B' })).toBeVisible()
  await page.getByLabel('回答 A', { exact: true }).check()
  await page.getByRole('button', { name: '提交盲评' }).click()
  const reviewProgress = page.locator('.evaluation-metrics article').filter({ hasText: '盲评进度' })
  await expect(reviewProgress.locator('strong')).toHaveText('1')
  await expect(reviewProgress.locator('small')).toHaveText('待我评审 0 份')
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
        channel_delivery: {
          attempts: 0,
          delivered: 0,
          degraded: 0,
          failed: 0,
          rate_limited: 0,
          failure_rate_percent: 0,
        },
        notification_delivery: {
          total: 0,
          pending: 0,
          running: 0,
          retrying: 0,
          succeeded: 0,
          failed: 0,
          dead_letters: 0,
        },
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
  await page.getByRole('textbox', { name: '智能体运行 ID' }).fill(runId)
  await page.getByRole('button', { name: '查询轨迹' }).click()
  await expect(page.getByText('7 步')).toBeVisible()
  await expect(page.getByText('记忆召回', { exact: true })).toBeVisible()
  await expect(page.getByText(/回复 · 92%/)).toBeVisible()
  await expect(page.getByText(/已完成 · 尝试 1/)).toBeVisible()
  await expect(page.getByText(/用途 对话自然表达/)).toBeVisible()
  await expect(page.getByText('策略已选')).toBeVisible()
  await expect(page.getByText('development / friendly-echo-v1').last()).toBeVisible()
  await expect(page.getByText('应用接口错误率')).toBeVisible()
  await expect(page.getByText('消息正文、完整提示词、隐藏推理')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
