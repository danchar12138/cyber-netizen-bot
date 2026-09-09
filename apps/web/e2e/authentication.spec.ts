import { expect, test } from './fixtures'

test('OIDC 模式在进入管理后台前显示安全登录边界', async ({ context, page }) => {
  await context.unroute('**/api/v1/auth/config')
  await context.route('**/api/v1/auth/config', async (route) => {
    await route.fulfill({
      json: {
        mode: 'oidc',
        authority: 'https://identity.example.test/realms/cnb',
        client_id: 'cyber-netizen-web',
        scope: 'openid profile email',
      },
    })
  })

  await page.goto('/')

  await expect(page.getByRole('heading', { name: '登录赛博网友控制平面' })).toBeVisible()
  await expect(page.getByRole('button', { name: '使用 OIDC 登录' })).toBeVisible()
  await expect(page.getByText('浏览器不会持有 client secret')).toBeVisible()
})
