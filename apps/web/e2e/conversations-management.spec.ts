import { expect, test } from '@playwright/test'

const conversationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const tenantId = '44444444-4444-4444-8444-444444444444'
const timestamp = '2026-09-09T08:00:00Z'

test('可以重命名、置顶、归档和软删除会话', async ({ page }) => {
  let title = '需要治理的会话'
  let status: 'active' | 'archived' = 'active'
  let pinnedAt: string | null = null
  let deleted = false

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
  await page.route('**/api/v1/chat/conversations?*', async (route) => {
    await route.fulfill({
      json: {
        items: deleted ? [] : [{
          id: conversationId,
          tenant_id: tenantId,
          agent_id: agentId,
          title,
          status,
          event_sequence: 1,
          created_at: timestamp,
          updated_at: timestamp,
          pinned_at: pinnedAt,
          archived_at: status === 'archived' ? timestamp : null,
          deleted_at: null,
          branched_from_conversation_id: null,
          branched_from_message_id: null,
        }],
        next_cursor: null,
      },
    })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}`, async (route) => {
    if (route.request().method() === 'DELETE') {
      deleted = true
    } else {
      const command = route.request().postDataJSON() as {
        title?: string
        status?: 'active' | 'archived'
        pinned?: boolean
      }
      if (command.title) title = command.title
      if (command.status) status = command.status
      if (command.pinned !== undefined) pinnedAt = command.pinned ? timestamp : null
    }
    await route.fulfill({
      json: {
        id: conversationId,
        tenant_id: tenantId,
        agent_id: agentId,
        title,
        status,
        event_sequence: 2,
        created_at: timestamp,
        updated_at: timestamp,
        pinned_at: pinnedAt,
        archived_at: status === 'archived' ? timestamp : null,
        deleted_at: deleted ? timestamp : null,
        branched_from_conversation_id: null,
        branched_from_message_id: null,
      },
    })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}/messages?limit=200`, async (route) => {
    await route.fulfill({
      json: {
        items: [{
          id: '55555555-5555-4555-8555-555555555555',
          conversation_id: conversationId,
          sender_type: 'user',
          sender_id: userId,
          content: '需要在管理页查看的消息',
          status: 'completed',
          client_message_id: '66666666-6666-4666-8666-666666666666',
          created_at: timestamp,
          updated_at: timestamp,
          edited_from_id: null,
        }],
        next_cursor: null,
      },
    })
  })
  await page.route(`**/api/v1/chat/conversations/${conversationId}/attachments`, async (route) => {
    await route.fulfill({ json: { items: [] } })
  })

  await page.goto('/conversations')
  await expect(page.getByText('需要治理的会话')).toBeVisible()
  await page.getByRole('button', { name: '查看消息' }).click()
  await expect(page.getByText('需要在管理页查看的消息')).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept('新的会话标题'))
  await page.getByRole('button', { name: '重命名 需要治理的会话' }).click()
  await expect(page.locator('.admin-table').getByText('新的会话标题', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '置顶', exact: true }).click()
  await expect(page.getByRole('button', { name: '取消置顶', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '归档', exact: true }).click()
  await expect(page.getByText('已归档 · 置顶')).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await expect(page.getByText('暂无匹配会话')).toBeVisible()
})
