import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

const conversationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const tenantId = '44444444-4444-4444-8444-444444444444'
const runId = '55555555-5555-4555-8555-555555555555'
const userMessageId = '66666666-6666-4666-8666-666666666666'
const responseMessageId = '77777777-7777-4777-8777-777777777777'
const attachmentId = '88888888-8888-4888-8888-888888888888'
const timestamp = '2026-09-09T08:00:00Z'

test('可以创建会话并发送一条持久化消息', async ({ page }) => {
  let created = false
  let sent = false
  const attachment = {
    id: attachmentId,
    conversation_id: conversationId,
    client_message_id: '',
    message_id: null as string | null,
    original_name: '说明.txt',
    content_type: 'text/plain',
    size_bytes: 12,
    sha256: '',
    status: 'ready',
    validation_error: null,
    created_at: timestamp,
    expires_at: '2026-09-09T08:10:00Z',
    uploaded_at: timestamp,
    attached_at: null as string | null,
  }
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['conversation:read', 'conversation:use'],
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
  await page.route('**/api/v1/chat/conversations?limit=100', async (route) => {
    await route.fulfill({
      json: {
        items: created
          ? [
              {
                id: conversationId,
                tenant_id: tenantId,
                agent_id: agentId,
                title: '新对话',
                status: 'active',
                event_sequence: 1,
                created_at: timestamp,
                updated_at: timestamp,
              },
            ]
          : [],
        next_cursor: null,
      },
    })
  })
  await page.route('**/api/v1/chat/conversations', async (route) => {
    created = true
    await route.fulfill({
      status: 201,
      json: {
        id: conversationId,
        tenant_id: tenantId,
        agent_id: agentId,
        title: '新对话',
        status: 'active',
        event_sequence: 1,
        created_at: timestamp,
        updated_at: timestamp,
      },
    })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}/messages?limit=200`, async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}/feedback`, async (route) => {
    await route.fulfill({ json: { items: [] } })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}/attachments`, async (route) => {
    await route.fulfill({ json: { items: sent ? [attachment] : [] } })
  })
  await page.route('**/api/v1/chat/attachments/reservations', async (route) => {
    const command = route.request().postDataJSON() as {
      client_message_id: string
      original_name: string
      sha256: string
    }
    expect(command.original_name).toBe('说明.txt')
    expect(command.sha256).toHaveLength(64)
    attachment.client_message_id = command.client_message_id
    attachment.sha256 = command.sha256
    await route.fulfill({
      status: 201,
      json: {
        attachment: { ...attachment, status: 'pending', uploaded_at: null },
        upload: {
          url: 'http://127.0.0.1:4173/mock-minio-upload',
          method: 'PUT',
          headers: {
            'Content-Type': 'text/plain',
            'x-amz-meta-sha256': command.sha256,
          },
          expires_at: attachment.expires_at,
        },
      },
    })
  })
  await page.route('**/mock-minio-upload', async (route) => {
    expect(route.request().method()).toBe('PUT')
    await route.fulfill({ status: 200, body: '' })
  })
  await page.route(`**/api/v1/chat/attachments/${attachmentId}/complete`, async (route) => {
    await route.fulfill({ json: attachment })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}/messages`, async (route) => {
    const command = route.request().postDataJSON() as {
      client_message_id: string
      content: string
      attachment_ids: string[]
    }
    expect(command.attachment_ids).toEqual([attachmentId])
    sent = true
    attachment.status = 'attached'
    attachment.message_id = userMessageId
    attachment.attached_at = timestamp
    await route.fulfill({
      status: 202,
      json: {
        user_message: {
          id: userMessageId,
          conversation_id: conversationId,
          sender_type: 'user',
          sender_id: userId,
          content: command.content,
          status: 'received',
          client_message_id: command.client_message_id,
          created_at: timestamp,
          updated_at: timestamp,
        },
        response_message: {
          id: responseMessageId,
          conversation_id: conversationId,
          sender_type: 'agent',
          sender_id: agentId,
          content: '',
          status: 'processing',
          client_message_id: null,
          created_at: '2026-09-09T08:00:00.000001Z',
          updated_at: '2026-09-09T08:00:00.000001Z',
        },
        run: {
          id: runId,
          conversation_id: conversationId,
          response_message_id: responseMessageId,
          status: 'queued',
          configuration_version: 0,
          persona_version: 1,
          prompt_version: 1,
          model_profile: 'development/friendly-echo-v1',
          input_tokens: null,
          output_tokens: null,
          error_code: null,
          created_at: timestamp,
          started_at: null,
          completed_at: null,
        },
        idempotent_replay: false,
      },
    })
  })
  await page.routeWebSocket('**/api/v1/chat/conversations/*/events?after=*', () => {})

  await page.goto('/chat')
  await page.getByRole('button', { name: '新建会话' }).click()
  await expect(page.getByText('从一句真心话开始吧')).toBeVisible()

  const composer = page.getByPlaceholder('输入消息，Enter 发送，Shift + Enter 换行')
  await composer.fill('这是一份自动保存的草稿')
  await page.reload()
  await expect(page.getByPlaceholder('输入消息，Enter 发送，Shift + Enter 换行')).toHaveValue('这是一份自动保存的草稿')

  await page.locator('input[type="file"]:not([accept])').setInputFiles({
    name: '说明.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('附件内容'),
  })
  await expect(page.getByText('已就绪')).toBeVisible()
  await page.getByPlaceholder('输入消息，Enter 发送，Shift + Enter 换行').fill('**你好**')
  await page.getByRole('button', { name: '发送消息' }).click()

  await expect(page.getByText('你好', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '说明.txt' })).toBeVisible()
  await expect(page.getByText('正在思考…')).toBeVisible()
  await expect(page.getByRole('button', { name: '停止生成' })).toBeVisible()

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])
})
