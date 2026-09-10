import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'

test('可以查看并搜索服务端角色能力矩阵', async ({ page }) => {
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['access_control:read'],
        authentication_mode: 'development',
      },
    })
  })
  await page.route('**/api/v1/administration/roles', async (route) => {
    await route.fulfill({
      json: {
        roles: [
          {
            role: 'admin',
            label: '管理员',
            description: '拥有全部管理、配置与密钥权限。',
            permissions: ['configuration:read', 'configuration:write', 'secret:manage'],
          },
          {
            role: 'viewer',
            label: '只读访客',
            description: '只能查看管理数据。',
            permissions: ['configuration:read', 'conversation:read'],
          },
        ],
      },
    })
  })

  await page.goto('/access')
  await expect(page.getByText('当前会话：本地开发者 · 管理员')).toBeVisible()
  await expect(page.getByText(/认证模式：开发身份/)).toBeVisible()
  const table = page.locator('.admin-table')
  await expect(table.getByText('管理员', { exact: true })).toBeVisible()
  await page.getByPlaceholder('搜索角色或权限').fill('viewer')
  await expect(table.getByText('只读访客', { exact: true })).toBeVisible()
  await expect(table.getByText('管理员', { exact: true })).not.toBeVisible()
})
