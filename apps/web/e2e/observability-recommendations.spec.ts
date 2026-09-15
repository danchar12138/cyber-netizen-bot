import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const acknowledgeLifecycleId = '55555555-5555-4555-8555-555555555555'
const observeLifecycleId = '66666666-6666-4666-8666-666666666666'
const feedbackId = '77777777-7777-4777-8777-777777777777'
const timestamp = '2026-09-15T08:00:00Z'

test('管理员可以反馈建议并查看质量事实', async ({ page }) => {
  const operationOrder: string[] = []
  const feedbackBodies = new Map<string, Record<string, unknown>>()
  let dispositionBody: Record<string, unknown> | null = null
  let acceptedFeedbackAttempts = 0
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '本地开发者',
      role: 'admin',
      permissions: ['trace:read', 'observability_alert:manage'],
      authentication_mode: 'development',
    } })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    await route.fulfill({ json: { items: [{
      id: agentId,
      tenant_id: tenantId,
      name: '赛博网友',
      status: 'active',
      created_at: timestamp,
    }], next_cursor: null } })
  })
  await page.route('**/api/v1/observability/dashboard', async (route) => {
    await route.fulfill({ json: {
      window_started_at: timestamp,
      window_ended_at: timestamp,
      api: { requests: 0, server_errors: 0, error_rate_percent: 0, latency: { p50_ms: 0, p95_ms: 0, p99_ms: 0 } },
      agent_runs: { terminal_runs: 0, completed_runs: 0, unsuccessful_runs: 0, success_rate_percent: 100, latency: { p50_ms: 0, p95_ms: 0, p99_ms: 0 } },
      models: [],
      queue: { backlog: 0, oldest_wait_seconds: 0 },
      channel_delivery: { attempts: 0, delivered: 0, degraded: 0, failed: 0, rate_limited: 0, failure_rate_percent: 0 },
      notification_delivery: { total: 0, pending: 0, running: 0, retrying: 0, succeeded: 0, failed: 0, dead_letters: 0 },
      total_estimated_cost_microusd: 0,
      alerts: [],
    } })
  })
  await page.route('**/api/v1/observability/alert-operations-summary', async (route) => {
    await route.fulfill({ json: {
      generated_at: timestamp,
      baseline: {
        window_started_at: timestamp,
        window_ended_at: timestamp,
        window_minutes: 480,
        periods: 7,
        sensitivity: 3,
        minimum_current_count: 3,
        signals: [],
      },
      handoff: {
        window_started_at: timestamp,
        window_ended_at: timestamp,
        active: 2,
        critical_active: 1,
        unacknowledged_active: 2,
        acknowledged_active: 0,
        suppressed_active: 0,
        opened: 2,
        resolved: 0,
        escalated: 1,
        blocked_replays: 0,
        sources: [],
        priority_items: [],
      },
    } })
  })
  await page.route('**/api/v1/observability/alert-recommendations/quality?*', async (route) => {
    const url = new URL(route.request().url())
    expect(Object.fromEntries(url.searchParams)).toEqual({ window_minutes: '10080' })
    await route.fulfill({ json: {
      window_started_at: '2026-09-08T08:00:00Z',
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
      actions: [
        { action: 'acknowledge', total: 2, accepted: 2, rejected: 0 },
        { action: 'observe', total: 2, accepted: 1, rejected: 1 },
      ],
      sources: [
        { source_type: 'agent_runtime', total: 3, accepted: 2, rejected: 1 },
        { source_type: 'api', total: 1, accepted: 1, rejected: 0 },
      ],
    } })
  })
  await page.route('**/api/v1/observability/alert-recommendations?*', async (route) => {
    const recommendations: Array<Record<string, unknown>> = []
    if (!feedbackBodies.has(acknowledgeLifecycleId)) {
      recommendations.push({
        lifecycle_id: acknowledgeLifecycleId,
        source_type: 'agent_runtime',
        source_key: 'agent_p95_latency',
        code: 'agent_p95_latency',
        severity: 'critical',
        action: 'acknowledge',
        priority: 'urgent',
        confidence: 0.9,
        reason_codes: ['critical', 'escalated'],
        guardrail_codes: [
          'manual_confirmation_required',
          'automatic_execution_forbidden',
          'current_scope_only',
        ],
        active_minutes: 90,
        occurrences: 4,
        escalation_level: 1,
        baseline_anomalous: false,
        suggested_suppression_minutes: null,
        requires_confirmation: true,
        automation_allowed: false,
      })
    }
    if (!feedbackBodies.has(observeLifecycleId)) {
      recommendations.push({
        lifecycle_id: observeLifecycleId,
        source_type: 'api',
        source_key: 'global',
        code: 'api_p95_latency',
        severity: 'warning',
        action: 'observe',
        priority: 'normal',
        confidence: 0.55,
        reason_codes: ['insufficient_signal'],
        guardrail_codes: [
          'manual_confirmation_required',
          'automatic_execution_forbidden',
          'current_scope_only',
        ],
        active_minutes: 10,
        occurrences: 1,
        escalation_level: 0,
        baseline_anomalous: false,
        suggested_suppression_minutes: null,
        requires_confirmation: true,
        automation_allowed: false,
      })
    }
    await route.fulfill({ json: recommendations })
  })
  await page.route('**/api/v1/observability/alert-lifecycles/metrics?*', async (route) => {
    await route.fulfill({ json: {
      window_started_at: timestamp,
      window_ended_at: timestamp,
      active: 2,
      opened: 2,
      resolved: 0,
      escalated: 1,
      mean_recovery_seconds: 0,
      p95_recovery_seconds: 0,
      sources: [],
      trend: [],
    } })
  })
  await page.route('**/api/v1/observability/alert-lifecycles/page?*', async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.route('**/api/v1/observability/alert-replay-reviews/metrics?*', async (route) => {
    await route.fulfill({ json: {
      window_started_at: timestamp,
      window_ended_at: timestamp,
      total: 0,
      allowed: 0,
      blocked: 0,
      allowed_rate_percent: 0,
      reasons: [],
      sources: [],
    } })
  })
  await page.route('**/api/v1/observability/alert-replay-reviews?*', async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.route(`**/api/v1/observability/alert-lifecycles/${acknowledgeLifecycleId}/acknowledge`, async (route) => {
    operationOrder.push('disposition')
    dispositionBody = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({ json: {
      lifecycle_id: acknowledgeLifecycleId,
      code: 'agent_p95_latency',
      source_type: 'agent_runtime',
      source_key: 'agent_p95_latency',
      status: 'acknowledged',
      reason: dispositionBody.reason,
      expires_at: null,
      updated_at: timestamp,
    } })
  })
  await page.route('**/api/v1/observability/alert-recommendations/*/feedback', async (route) => {
    const lifecycleId = route.request().url().split('/').at(-2)!
    const body = route.request().postDataJSON() as Record<string, unknown>
    operationOrder.push(`feedback:${body.decision as string}`)
    if (body.decision === 'accepted' && acceptedFeedbackAttempts++ === 0) {
      await route.fulfill({
        status: 503,
        json: {
          schema_version: '1',
          error: {
            code: 'service_unavailable',
            message: '建议反馈暂时不可用',
            request_id: 'request-id',
          },
        },
      })
      return
    }
    feedbackBodies.set(lifecycleId, body)
    const isAcknowledgement = lifecycleId === acknowledgeLifecycleId
    await route.fulfill({ json: {
      id: feedbackId,
      lifecycle_id: lifecycleId,
      source_type: isAcknowledgement ? 'agent_runtime' : 'api',
      source_key: isAcknowledgement ? 'agent_p95_latency' : 'global',
      code: isAcknowledgement ? 'agent_p95_latency' : 'api_p95_latency',
      recommendation_action: isAcknowledgement ? 'acknowledge' : 'observe',
      priority: isAcknowledgement ? 'urgent' : 'normal',
      reason_codes: isAcknowledgement ? ['critical', 'escalated'] : ['insufficient_signal'],
      decision: body.decision,
      actor_id: userId,
      feedback_at: timestamp,
    } })
  })

  await page.goto('/observability')
  await expect(page.getByRole('heading', { name: '告警处置建议' })).toBeVisible()
  await expect(page.getByText(/禁止自动执行/).first()).toBeVisible()
  await expect(page.getByText('智能体运行耗时偏高', { exact: true })).toBeVisible()
  await expect(page.getByText('严重告警 · 已经升级', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '建议质量概览' })).toBeVisible()
  await expect(page.getByText('75.00%', { exact: true })).toBeVisible()
  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm')
    expect(dialog.message()).toContain('建议不会自动执行')
    await dialog.accept()
  })
  await page.getByRole('button', { name: '确认并接手调查' }).click()

  await expect(page.getByRole('alert')).toContainText('建议反馈暂时不可用')
  expect(operationOrder).toEqual(['disposition', 'feedback:accepted'])
  page.once('dialog', async (dialog) => {
    await dialog.accept()
  })
  await page.getByRole('button', { name: '确认并接手调查' }).click()

  await expect(page.getByRole('status')).toContainText('建议反馈已记录')
  expect(dispositionBody).toEqual({
    confirmed: true,
    reason: '人工采纳处置建议：严重告警、已经升级',
  })
  expect(feedbackBodies.get(acknowledgeLifecycleId)).toEqual({
    decision: 'accepted',
    confirmed: true,
  })
  expect(operationOrder).toEqual([
    'disposition',
    'feedback:accepted',
    'feedback:accepted',
  ])

  await expect(page.getByText('应用接口响应偏慢', { exact: true })).toBeVisible()
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('不会触发告警处置')
    await dialog.accept()
  })
  await page.getByRole('button', { name: '驳回建议' }).click()

  await expect(page.getByRole('status')).toContainText('已驳回“继续观察”建议')
  expect(feedbackBodies.get(observeLifecycleId)).toEqual({
    decision: 'rejected',
    confirmed: true,
  })
  expect(operationOrder).toEqual([
    'disposition',
    'feedback:accepted',
    'feedback:accepted',
    'feedback:rejected',
  ])
})
