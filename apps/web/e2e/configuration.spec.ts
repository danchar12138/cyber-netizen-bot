import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const versionId = '11111111-1111-4111-8111-111111111111'
const secretId = '55555555-5555-4555-8555-555555555555'
const timestamp = '2026-09-09T08:00:00Z'

test('可以预览并发布配置差异以及安全写入密钥', async ({ page }) => {
  let versionStatus: 'draft' | 'published' | null = null
  let secretConfigured = false
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['configuration:read', 'configuration:write', 'secret:manage'],
        authentication_mode: 'development',
      },
    })
  })
  await page.route('**/api/v1/chat/identity', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        agent_id: agentId,
        user_name: '本地开发者',
        agent_name: '赛博网友',
      },
    })
  })
  await page.route('**/api/v1/configuration/definitions', async (route) => {
    await route.fulfill({
      json: {
        schema_version: '1',
        definitions: [
          {
            key: 'model.chat.provider',
            section: 'model',
            label: '对话模型 Provider',
            description: '对话运行使用的模型 Provider。',
            value_kind: 'string',
            default: 'development',
            scopes: ['system', 'tenant', 'agent', 'channel'],
            secret: false,
            hot_reload: true,
            minimum: null,
            maximum: null,
            options: ['development', 'openai'],
          },
          {
            key: 'model.openai.api_key',
            section: 'model',
            label: 'OpenAI API 密钥',
            description: 'Responses API 访问密钥。',
            value_kind: 'secret',
            default: null,
            scopes: ['system', 'tenant', 'agent'],
            secret: true,
            hot_reload: true,
            minimum: null,
            maximum: null,
            options: [],
          },
        ],
      },
    })
  })
  await page.route('**/api/v1/configuration/versions', async (route) => {
    await route.fulfill({
      json: {
        versions: versionStatus
          ? [{
              id: versionId,
              version: 1,
              status: versionStatus,
              note: '切换正式模型',
              created_at: timestamp,
              published_at: versionStatus === 'published' ? timestamp : null,
              values: [{
                key: 'model.chat.provider',
                scope_type: 'system',
                scope_id: null,
                value: 'openai',
              }],
            }]
          : [],
      },
    })
  })
  await page.route('**/api/v1/configuration/effective?*', async (route) => {
    await route.fulfill({
      json: {
        version: versionStatus === 'published' ? 1 : 0,
        values: [{
          key: 'model.chat.provider',
          value: versionStatus === 'published' ? 'openai' : 'development',
          source: {
            scope_type: versionStatus === 'published' ? 'system' : null,
            scope_id: null,
            version: versionStatus === 'published' ? 1 : 0,
          },
        }],
      },
    })
  })
  await page.route('**/api/v1/configuration/drafts', async (route) => {
    versionStatus = 'draft'
    await route.fulfill({
      status: 201,
      json: {
        id: versionId,
        version: 1,
        status: 'draft',
        note: '切换正式模型',
        created_at: timestamp,
        published_at: null,
        values: [{
          key: 'model.chat.provider',
          scope_type: 'system',
          scope_id: null,
          value: 'openai',
        }],
      },
    })
  })
  await page.route(`**/api/v1/configuration/versions/${versionId}/diff`, async (route) => {
    await route.fulfill({
      json: {
        base_version: 0,
        target_version: 1,
        changes: [{
          key: 'model.chat.provider',
          scope_type: 'system',
          scope_id: null,
          kind: 'added',
          before: null,
          after: 'openai',
        }],
      },
    })
  })
  await page.route(`**/api/v1/configuration/versions/${versionId}/publish`, async (route) => {
    versionStatus = 'published'
    await route.fulfill({
      json: {
        id: versionId,
        version: 1,
        status: 'published',
        note: '切换正式模型',
        created_at: timestamp,
        published_at: timestamp,
        values: [{
          key: 'model.chat.provider',
          scope_type: 'system',
          scope_id: null,
          value: 'openai',
        }],
      },
    })
  })
  await page.route('**/api/v1/configuration/secrets', async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { plaintext: string }
      expect(body.plaintext).toBe('仅供浏览器测试的虚假凭证')
      secretConfigured = true
      await route.fulfill({ json: secretMetadata() })
      return
    }
    await route.fulfill({ json: { secrets: secretConfigured ? [secretMetadata()] : [] } })
  })

  await page.goto('/configuration')
  await page.getByRole('combobox').nth(1).selectOption('openai')
  await page.getByPlaceholder('本次修改说明（可选）').fill('切换正式模型')
  await page.getByRole('button', { name: '保存新草稿' }).click()
  await expect(page.getByText(/草稿 v1 已保存/)).toBeVisible()
  await expect(page.getByText('发布差异', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '校验并发布' }).click()
  await expect(page.getByText('已发布')).toBeVisible()

  await page.getByRole('button', { name: '密钥管理' }).click()
  await page.getByPlaceholder('输入密钥').fill('仅供浏览器测试的虚假凭证')
  await page.getByRole('button', { name: '写入' }).click()
  await expect(page.getByText(/••••虚假凭证/)).toBeVisible()
  await expect(page.getByPlaceholder('输入新值以轮换')).toHaveValue('')
})

function secretMetadata() {
  return {
    id: secretId,
    key: 'model.openai.api_key',
    scope_type: 'system',
    scope_id: null,
    provider: 'aes_gcm',
    configured: true,
    masked_hint: '••••虚假凭证',
    integrity_status: 'untested',
    created_at: timestamp,
    updated_at: timestamp,
    last_tested_at: null,
  }
}
