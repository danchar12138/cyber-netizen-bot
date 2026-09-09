import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

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
        permissions: ['dashboard:read'],
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
        worker: {
          name: 'worker',
          status: 'not_checked',
          detail: '尚未建立 Worker 心跳；P5 将接入任务明细与重放能力。',
        },
      },
    })
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
  await expect(page.getByText('Dramatiq', { exact: true })).toBeVisible()
  await expect(page.getByText('当前页面不伪造工作进程在线状态')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
