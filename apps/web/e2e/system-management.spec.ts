import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'

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
          detail: '尚未建立 Worker 心跳；P5 将接入任务明细与重放能力。',
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
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
