import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const timestamp = '2026-09-10T08:00:00Z'

test('运行总览聚合健康、质量、任务、渠道与告警', async ({ page }) => {
  const channelRequestAgents: Array<string | undefined> = []
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '本地开发者',
      role: 'admin',
      permissions: ['dashboard:read', 'agent:read', 'trace:read', 'channel:read'],
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
  await page.route('**/api/v1/system/overview', async (route) => {
    await route.fulfill({ json: {
      environment: 'development',
      version: '0.1.0',
      active_agents: 1,
      active_conversations: 3,
      pending_jobs: 5,
      configuration_definitions: 41,
      components: [
        { name: 'api', status: 'healthy', detail: null },
        { name: 'postgresql', status: 'healthy', detail: null },
        { name: 'redis', status: 'degraded', detail: 'ConnectionError' },
        { name: 'object_storage', status: 'healthy', detail: null },
      ],
    } })
  })
  await page.route('**/api/v1/system/tasks/status', async (route) => {
    await route.fulfill({ json: {
      broker: 'dramatiq-redis',
      queues: ['system', 'memory', 'reflection', 'proactive'],
      pending_jobs: 4,
      running_jobs: 2,
      retrying_jobs: 1,
      dead_letter_jobs: 1,
      scheduled_actions: 3,
      worker: { name: 'worker', status: 'healthy', detail: '2 个任务进程心跳正常。' },
    } })
  })
  await page.route('**/api/v1/observability/dashboard', async (route) => {
    await route.fulfill({ json: {
      window_started_at: timestamp,
      window_ended_at: '2026-09-10T09:00:00Z',
      api: {
        requests: 200,
        server_errors: 2,
        error_rate_percent: 1,
        latency: { p50_ms: 20, p95_ms: 80, p99_ms: 120 },
      },
      agent_runs: {
        terminal_runs: 20,
        completed_runs: 19,
        unsuccessful_runs: 1,
        success_rate_percent: 95,
        latency: { p50_ms: 600, p95_ms: 1400, p99_ms: 1800 },
      },
      models: [{
        provider: 'openai',
        model: 'gpt-5',
        invocations: 12,
        failed_invocations: 1,
        input_tokens: 8000,
        output_tokens: 2500,
        estimated_cost_microusd: 12345,
        latency: { p50_ms: 400, p95_ms: 900, p99_ms: 1300 },
      }],
      queue: { backlog: 5, oldest_wait_seconds: 12 },
      total_estimated_cost_microusd: 12345,
      alerts: [{
        code: 'api_error_rate',
        severity: 'warning',
        title: 'API 错误率偏高',
        summary: '当前窗口的 API 5xx 错误率超过告警阈值。',
        current_value: 1,
        threshold_value: 0.5,
        unit: '%',
      }],
    } })
  })
  await page.route('**/api/v1/channels', async (route) => {
    channelRequestAgents.push(route.request().headers()['x-cnb-agent-id'])
    await route.fulfill({ json: { items: [{
      id: '55555555-5555-4555-8555-555555555555',
      tenant_id: tenantId,
      agent_id: agentId,
      name: '内部 Web',
      platform: 'web',
      display_name: '内部 Web',
      implementation_status: 'ready',
      status: 'enabled',
      rate_limit_per_minute: 60,
      settings: {},
      credential_configured: false,
      capabilities: {
        text: true, markdown: true, images: true, files: true, streaming: true,
        reactions: true, threads: true, message_edit: true, proactive_messages: true,
        max_text_chars: 20000, max_blocks: 20, max_attachment_bytes: 262144000,
        accepted_content_types: [],
      },
      health_status: 'healthy',
      health_detail: '内部 Web 适配器已就绪。',
      last_checked_at: timestamp,
      created_by: userId,
      created_at: timestamp,
      updated_at: timestamp,
    }] } })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '系统状态、运行质量与风险，一页掌握。' })).toBeVisible()
  await expect(page.getByText('95.00%', { exact: true })).toBeVisible()
  await expect(page.getByText('10,500 词元 · 当前智能体', { exact: true })).toBeVisible()
  await expect(page.getByText('API 错误率偏高', { exact: true })).toBeVisible()
  await expect(page.getByText('ConnectionError', { exact: true })).toBeVisible()
  await expect(page.getByText('2 个任务进程心跳正常。', { exact: true })).toBeVisible()
  await expect(page.getByRole('group', { name: '渠道健康摘要' })).toContainText('健康')
  await expect.poll(() => channelRequestAgents).toContain(agentId)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})

test('可以查看安全启动设置和真实任务基础状态', async ({ page }) => {
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['dashboard:read', 'task:read', 'task:manage', 'proactive:manage'],
        authentication_mode: 'development',
      },
    })
  })
  await page.route('**/api/v1/system/settings', async (route) => {
    await route.fulfill({
      json: {
        environment: 'development',
        log_level: 'INFO',
        cors_origins: ['http://localhost:5173'],
        readiness_deep_checks: false,
        authentication_mode: 'development',
        oidc_configured: false,
        otel_enabled: true,
        otel_exporter_configured: true,
        otel_service_name: 'cyber-netizen-api',
        otel_trace_sample_ratio: 0.1,
        object_storage_provider: 'minio',
        minio_endpoint_url: 'http://localhost:9000',
        minio_bucket: 'cyber-netizen',
        database_configured: true,
        redis_configured: true,
        minio_credentials_configured: true,
        config_master_key_status: 'configured',
        requires_restart: true,
      },
    })
  })
  await page.route('**/api/v1/system/tasks/status', async (route) => {
    await route.fulfill({
      json: {
        broker: 'dramatiq-redis',
        queues: ['system'],
        pending_jobs: 0,
        running_jobs: 0,
        retrying_jobs: 0,
        dead_letter_jobs: 0,
        scheduled_actions: 0,
        worker: {
          name: 'worker',
          status: 'not_checked',
          detail: '尚未建立任务进程心跳。',
        },
      },
    })
  })
  await page.route('**/api/v1/chat/identity', async (route) => {
    await route.fulfill({ json: { tenant_id: tenantId, user_id: userId, agent_id: '33333333-3333-4333-8333-333333333333', user_name: '本地开发者', agent_name: '赛博网友' } })
  })
  await page.route('**/api/v1/tasks/dashboard', async (route) => {
    await route.fulfill({ json: { pending: 1, running: 0, retrying: 0, dead_letters: 0, scheduled: 0, workers: [] } })
  })
  await page.route('**/api/v1/tasks/jobs?*', async (route) => {
    await route.fulfill({ json: { items: [] } })
  })
  await page.route('**/api/v1/tasks/scheduled-actions?*', async (route) => {
    await route.fulfill({ json: { items: [] } })
  })

  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: '系统设置' })).toBeVisible()
  await expect(page.getByText('MinIO', { exact: true })).toBeVisible()
  await expect(page.getByText('开发环境', { exact: true })).toBeVisible()
  await expect(page.getByText('信息', { exact: true })).toBeVisible()
  await expect(page.getByText('遥测数据导出器（OTLP）')).toBeVisible()
  await expect(page.getByText('cyber-netizen', { exact: true })).toBeVisible()
  await expect(page.getByText('页面永不返回数据库、Redis、MinIO 或配置主密钥明文。')).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: '跳转到主要内容' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main-content')).toBeFocused()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])

  await page.goto('/tasks')
  await expect(page.getByRole('heading', { name: '任务与主动行为' })).toBeVisible()
  await expect(page.getByText('PostgreSQL 保存任务真相；Dramatiq/Redis 负责投递，重复消息由租约、去重键和尝试记录吸收。')).toBeVisible()
  await expect(page.getByText('主动消息默认关闭，当前只分发到安全边界')).toBeVisible()
  await expect(page.getByText(/再交给统一渠道适配器发送/)).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
