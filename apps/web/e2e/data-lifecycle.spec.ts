import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const timestamp = '2026-09-10T08:00:00Z'

function run(kind: string, overrides: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(),
    tenant_id: tenantId,
    actor_id: userId,
    subject_user_id: null,
    kind,
    status: 'succeeded',
    counters: {},
    evidence: {},
    error_code: null,
    started_at: timestamp,
    completed_at: timestamp,
    ...overrides,
  }
}

test('可以管理数据生命周期并登记隔离恢复证据', async ({ page }) => {
  const runs: Array<ReturnType<typeof run>> = []
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '本地开发者',
      role: 'admin',
      permissions: [
        'data_lifecycle:read',
        'data_lifecycle:export',
        'data_lifecycle:forget',
        'data_lifecycle:retention_manage',
        'data_lifecycle:backup_drill_record',
      ],
      authentication_mode: 'development',
    } })
  })
  await page.route('**/api/v1/data-lifecycle/overview', async (route) => {
    await route.fulfill({ json: {
      policy: {
        deleted_conversation_days: 30,
        deleted_attachment_days: 7,
        orphan_grace_hours: 24,
        batch_size: 100,
        export_max_records: 10000,
        export_max_bytes: 26214400,
        backup_expected_interval_hours: 720,
      },
      runs,
    } })
  })
  await page.route('**/api/v1/data-lifecycle/exports', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ user_id: userId })
    runs.unshift(run('user_export', {
      id: '55555555-5555-4555-8555-555555555555',
      subject_user_id: userId,
      counters: { records: 3, bytes: 256 },
      evidence: { schema_version: 'cnb-user-export-v1', sha256: 'a'.repeat(64) },
    }))
    await route.fulfill({
      body: JSON.stringify({ schema_version: 'cnb-user-export-v1', data: { profile: {} } }),
      contentType: 'application/json',
      headers: {
        'Content-Disposition': `attachment; filename="cyber-netizen-user-${userId}.json"`,
        'X-Content-SHA256': 'a'.repeat(64),
        'X-Export-Run-ID': '55555555-5555-4555-8555-555555555555',
      },
    })
  })
  await page.route('**/api/v1/data-lifecycle/forget', async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      user_id: userId,
      confirmation: `FORGET ${userId}`,
    })
    const result = run('user_forget', {
      subject_user_id: userId,
      counters: { users_redacted: 1, objects_failed: 0 },
      evidence: { database_redaction_completed: true, objects_deleted: true },
    })
    runs.unshift(result)
    await route.fulfill({ json: result })
  })
  await page.route('**/api/v1/data-lifecycle/retention/cleanup', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmed: true })
    const result = run('retention_cleanup', { counters: { conversations_purged: 2 } })
    runs.unshift(result)
    await route.fulfill({ json: result })
  })
  await page.route('**/api/v1/data-lifecycle/objects/orphans/cleanup', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmed: true })
    const result = run('orphan_cleanup', { counters: { objects_scanned: 5, objects_deleted: 1 } })
    runs.unshift(result)
    await route.fulfill({ json: result })
  })
  await page.route('**/api/v1/data-lifecycle/backup-drills', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      manifest_sha256: 'b'.repeat(64),
      database_rows_verified: 120,
      objects_verified: 8,
      database_integrity_verified: true,
      object_integrity_verified: true,
      application_smoke_verified: true,
      confirmation: 'BACKUP RESTORE VERIFIED',
    })
    const result = run('backup_restore_drill', {
      counters: { database_rows_verified: 120, objects_verified: 8 },
      evidence: { manifest_sha256: 'b'.repeat(64), isolated_restore_required: true },
      completed_at: new Date().toISOString(),
    })
    runs.unshift(result)
    await route.fulfill({ json: result })
  })

  await page.goto('/data-lifecycle')
  await expect(page.getByRole('heading', { name: '数据生命周期' })).toBeVisible()
  await expect(page.getByText('导出仅包含显式白名单字段')).toBeVisible()
  await expect(page.getByText('尚无有效期内的隔离恢复演练')).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])

  await page.getByLabel('目标用户 UUID', { exact: true }).fill(userId)
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 JSON 导出' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe(`cyber-netizen-user-${userId}.json`)
  await expect(page.getByText(/导出已生成并开始下载/)).toBeVisible()

  await page.locator('.lifecycle-danger-zone input').fill(`FORGET ${userId}`)
  await page.getByRole('button', { name: '永久遗忘用户数据' }).click()
  await expect(page.getByText('用户正文、身份绑定、记忆、关系与私有对象已按策略处理。')).toBeVisible()

  await page.getByLabel('我确认执行').nth(0).check()
  await page.getByRole('button', { name: '执行保留期清理' }).click()
  await expect(page.getByText('保留期清理已完成，结果已登记到安全运行证据。')).toBeVisible()
  await page.getByLabel('我确认执行').nth(1).check()
  await page.getByRole('button', { name: '扫描并清理孤儿' }).click()
  await expect(page.getByText('MinIO 孤儿扫描已完成，新对象和已引用对象均受保护。')).toBeVisible()

  await page.getByLabel('备份清单 SHA-256').fill('b'.repeat(64))
  await page.getByLabel('数据库校验行数').fill('120')
  await page.getByLabel('对象校验数量').fill('8')
  await page.getByLabel('PostgreSQL 完整性').check()
  await page.getByLabel('MinIO 对象完整性').check()
  await page.getByLabel('应用冒烟').check()
  await page.locator('.lifecycle-confirmation input').fill('BACKUP RESTORE VERIFIED')
  await page.getByRole('button', { name: '登记演练证据' }).click()
  await expect(page.getByText('隔离恢复演练证据已登记。')).toBeVisible()
  await expect(page.getByText('备份恢复演练').first()).toBeVisible()
})
