import { expect, test as base } from '@playwright/test'

export type { Page } from '@playwright/test'
export { expect }

export const test = base.extend<{ authenticationBootstrap: void }>({
  authenticationBootstrap: [async ({ context }, use) => {
    await context.route('**/api/v1/auth/config', async (route) => {
      await route.fulfill({
        json: {
          mode: 'development',
          authority: null,
          client_id: null,
          scope: null,
        },
      })
    })
    await use()
  }, { auto: true }],
})
