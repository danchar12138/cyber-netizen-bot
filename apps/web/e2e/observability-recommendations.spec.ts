import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const lifecycleId = '55555555-5555-4555-8555-555555555555'
const timestamp = '2026-09-15T08:00:00Z'

test('管理员可以审阅并人工确认告警处置建议', async ({ page }) => {
  let acknowledged = false
  let dispositionBody: Record<string, unknown> | null = null
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
        active: 1,
        critical_active: 1,
        unacknowledged_active: 1,
        acknowledged_active: 0,
        suppressed_active: 0,
        opened: 1,
        resolved: 0,
        escalated: 1,
        blocked_replays: 0,
        sources: [],
        priority_items: [],
      },
    } })
  })
  await page.route('**/api/v1/observability/alert-recommendations?*', async (route) => {
    await route.fulfill({ json: acknowledged ? [] : [{
      lifecycle_id: lifecycleId,
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
    }] })
  })
  await page.route('**/api/v1/observability/alert-lifecycles/metrics?*', async (route) => {
    await route.fulfill({ json: {
      window_started_at: timestamp,
      window_ended_at: timestamp,
      active: 1,
      opened: 1,
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
  await page.route(`**/api/v1/observability/alert-lifecycles/${lifecycleId}/acknowledge`, async (route) => {
    dispositionBody = route.request().postDataJSON() as Record<string, unknown>
    acknowledged = true
    await route.fulfill({ json: {
      lifecycle_id: lifecycleId,
      code: 'agent_p95_latency',
      source_type: 'agent_runtime',
      source_key: 'agent_p95_latency',
      status: 'acknowledged',
      reason: dispositionBody.reason,
      expires_at: null,
      updated_at: timestamp,
    } })
  })

  await page.goto('/observability')
  await expect(page.getByRole('heading', { name: '告警处置建议' })).toBeVisible()
  await expect(page.getByText(/禁止自动执行/).first()).toBeVisible()
  await expect(page.getByText('智能体运行耗时偏高', { exact: true })).toBeVisible()
  await expect(page.getByText('严重告警 · 已经升级', { exact: true })).toBeVisible()
  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm')
    expect(dialog.message()).toContain('建议不会自动执行')
    await dialog.accept()
  })
  await page.getByRole('button', { name: '确认并接手调查' }).click()

  await expect(page.getByRole('status')).toContainText('告警处置已更新')
  expect(dispositionBody).toEqual({
    confirmed: true,
    reason: '人工采纳处置建议：严重告警、已经升级',
  })
})
