import AxeBuilder from '@axe-core/playwright'

import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const otherAgentId = '77777777-7777-4777-8777-777777777777'
const channelId = '55555555-5555-4555-8555-555555555555'
const conversationId = '88888888-8888-4888-8888-888888888888'
const identityMappingId = '99999999-9999-4999-8999-999999999999'
const conversationMappingId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const inboxId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const jobId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const replayJobId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
const timestamp = '2026-09-10T08:00:00Z'

const capabilities = {
  text: true,
  markdown: true,
  images: true,
  files: true,
  streaming: true,
  reactions: true,
  threads: true,
  message_edit: true,
  proactive_messages: true,
  max_text_chars: 20_000,
  max_blocks: 20,
  max_attachment_bytes: 262_144_000,
  accepted_content_types: ['image/png', 'application/pdf'],
}

test('可以管理外部映射、查看 Inbox 摘要并按 Agent 隔离', async ({ page }) => {
  const scopedAgents: Array<string | undefined> = []
  const identityMappings: Array<Record<string, unknown>> = []
  const conversationMappings: Array<Record<string, unknown>> = []
  let replayed = false

  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '本地开发者',
      role: 'admin',
      permissions: [
        'agent:read', 'channel:read', 'conversation:read', 'user:read',
        'integration:read', 'integration:manage', 'inbox:replay',
      ],
      authentication_mode: 'development',
    } })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    await route.fulfill({ json: { items: [
      { id: agentId, tenant_id: tenantId, name: '赛博网友', status: 'active', created_at: timestamp },
      { id: otherAgentId, tenant_id: tenantId, name: '安静伙伴', status: 'active', created_at: timestamp },
    ], next_cursor: null } })
  })
  await page.route('**/api/v1/administration/users?*', async (route) => {
    await route.fulfill({ json: { items: [{
      id: userId,
      tenant_id: tenantId,
      display_name: '本地开发者',
      status: 'active',
      created_at: timestamp,
    }], next_cursor: null } })
  })
  await page.route('**/api/v1/chat/conversations?*', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedAgents.push(selectedAgent)
    await route.fulfill({ json: {
      items: selectedAgent === otherAgentId ? [] : [{
        id: conversationId,
        tenant_id: tenantId,
        agent_id: agentId,
        title: '外部联调会话',
        status: 'active',
        event_sequence: 1,
        created_at: timestamp,
        updated_at: timestamp,
        pinned_at: null,
        archived_at: null,
        deleted_at: null,
        branched_from_conversation_id: null,
        branched_from_message_id: null,
      }],
      next_cursor: null,
    } })
  })
  const channel = {
    id: channelId,
    tenant_id: tenantId,
    agent_id: agentId,
    name: '内部 Web',
    platform: 'web',
    display_name: '内部 Web',
    implementation_status: 'ready',
    status: 'enabled',
    rate_limit_per_minute: 60,
    settings: {},
    credential_configured: true,
    capabilities,
    health_status: 'healthy',
    health_detail: null,
    last_checked_at: timestamp,
    created_by: userId,
    created_at: timestamp,
    updated_at: timestamp,
  }
  await page.route('**/api/v1/channels', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedAgents.push(selectedAgent)
    await route.fulfill({ json: { items: selectedAgent === otherAgentId ? [] : [channel] } })
  })
  await page.route('**/api/v1/integrations/identity-mappings?*', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedAgents.push(selectedAgent)
    await route.fulfill({ json: {
      items: selectedAgent === otherAgentId ? [] : identityMappings,
    } })
  })
  await page.route('**/api/v1/integrations/identity-mappings', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    expect(route.request().postDataJSON()).toEqual({
      channel_id: channelId,
      external_subject_id: 'web-user-42',
      user_id: userId,
    })
    const mapping = {
      id: identityMappingId,
      tenant_id: tenantId,
      agent_id: agentId,
      channel_id: channelId,
      platform: 'web',
      external_subject_id: 'web-user-42',
      user_id: userId,
      status: 'enabled',
      created_by: userId,
      created_at: timestamp,
      updated_at: timestamp,
    }
    identityMappings.push(mapping)
    await route.fulfill({ status: 201, json: mapping })
  })
  await page.route('**/api/v1/integrations/conversation-mappings?*', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedAgents.push(selectedAgent)
    await route.fulfill({ json: {
      items: selectedAgent === otherAgentId ? [] : conversationMappings,
    } })
  })
  await page.route('**/api/v1/integrations/conversation-mappings', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    expect(route.request().postDataJSON()).toEqual({
      channel_id: channelId,
      user_id: userId,
      kind: 'direct',
      external_conversation_id: 'web-conversation-42',
      external_thread_id: 'thread-7',
      conversation_id: conversationId,
    })
    const mapping = {
      id: conversationMappingId,
      tenant_id: tenantId,
      agent_id: agentId,
      channel_id: channelId,
      platform: 'web',
      kind: 'direct',
      external_conversation_id: 'web-conversation-42',
      external_thread_id: 'thread-7',
      conversation_id: conversationId,
      status: 'enabled',
      created_by: userId,
      created_at: timestamp,
      updated_at: timestamp,
    }
    conversationMappings.push(mapping)
    await route.fulfill({ status: 201, json: mapping })
  })
  await page.route('**/api/v1/integrations/inbox?*', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedAgents.push(selectedAgent)
    await route.fulfill({ json: { items: selectedAgent === otherAgentId ? [] : [{
      id: inboxId,
      agent_id: agentId,
      channel_id: channelId,
      schema_version: '1',
      platform: 'web',
      event_type: 'message.created',
      status: 'dead_letter',
      job_id: jobId,
      job_status: 'dead_letter',
      external_event_digest: '1'.repeat(64),
      external_subject_digest: '2'.repeat(64),
      external_conversation_digest: '3'.repeat(64),
      external_thread_digest: '4'.repeat(64),
      external_message_digest: '5'.repeat(64),
      user_id: userId,
      conversation_id: conversationId,
      content_kinds: ['text', 'image'],
      content_block_count: 2,
      received_at: timestamp,
      processed_at: timestamp,
      last_error_code: 'TemporaryFailure',
    }] } })
  })
  await page.route(`**/api/v1/integrations/inbox/${inboxId}/replay`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      confirmed: true,
      reason: '管理员确认映射与处理条件已恢复',
    })
    replayed = true
    await route.fulfill({ json: {
      job_id: replayJobId,
      status: 'pending',
      replayed_from_id: jobId,
    } })
  })

  await page.goto('/integrations')
  await expect(page.getByRole('heading', { name: '外部身份与 Inbox' })).toBeVisible()
  await expect(page.getByText('诊断页不展示消息正文、凭证、对象键或远端响应')).toBeVisible()
  await expect(page.getByText('2 块 · text、image')).toBeVisible()

  await page.getByLabel('渠道实例').first().selectOption(channelId)
  await page.getByPlaceholder('只使用平台稳定 ID').fill('web-user-42')
  await page.getByLabel('本地用户').first().selectOption(userId)
  await page.getByRole('button', { name: '创建身份映射' }).click()
  await expect(page.getByRole('table').getByText('web-user-42')).toBeVisible()

  await page.getByLabel('渠道实例').nth(1).selectOption(channelId)
  await page.getByLabel('本地用户').nth(1).selectOption(userId)
  await page.getByLabel('内部会话', { exact: true }).selectOption(conversationId)
  await page.getByLabel('外部会话 ID').fill('web-conversation-42')
  await page.getByLabel('外部线程 ID（可选）').fill('thread-7')
  await page.getByRole('button', { name: '创建路由映射' }).click()
  await expect(page.getByRole('table').getByText('web-conversation-42')).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '重放' }).click()
  await expect.poll(() => replayed).toBe(true)

  await page.getByLabel('当前 Agent').selectOption(otherAgentId)
  await expect(page.getByText('尚无身份映射')).toBeVisible()
  await expect(page.getByText('尚无会话映射')).toBeVisible()
  await expect(page.getByText('尚无入站事件')).toBeVisible()
  expect(scopedAgents).toContain(agentId)
  expect(scopedAgents).toContain(otherAgentId)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})

test('只读权限不允许修改映射或重放 Inbox', async ({ page }) => {
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '只读访客',
      role: 'viewer',
      permissions: ['agent:read', 'channel:read', 'conversation:read', 'user:read', 'integration:read'],
      authentication_mode: 'development',
    } })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    await route.fulfill({ json: { items: [
      { id: agentId, tenant_id: tenantId, name: '赛博网友', status: 'active', created_at: timestamp },
    ], next_cursor: null } })
  })
  await page.route('**/api/v1/administration/users?*', async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.route('**/api/v1/chat/conversations?*', async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.route('**/api/v1/channels', async (route) => {
    await route.fulfill({ json: { items: [] } })
  })
  await page.route('**/api/v1/integrations/identity-mappings?*', async (route) => {
    await route.fulfill({ json: { items: [] } })
  })
  await page.route('**/api/v1/integrations/conversation-mappings?*', async (route) => {
    await route.fulfill({ json: { items: [] } })
  })
  await page.route('**/api/v1/integrations/inbox?*', async (route) => {
    await route.fulfill({ json: { items: [{
      id: inboxId,
      agent_id: agentId,
      channel_id: channelId,
      schema_version: '1',
      platform: 'web',
      event_type: 'message.created',
      status: 'dead_letter',
      job_id: jobId,
      job_status: 'dead_letter',
      external_event_digest: '1'.repeat(64),
      external_subject_digest: '2'.repeat(64),
      external_conversation_digest: '3'.repeat(64),
      external_thread_digest: null,
      external_message_digest: '5'.repeat(64),
      user_id: userId,
      conversation_id: conversationId,
      content_kinds: ['text'],
      content_block_count: 1,
      received_at: timestamp,
      processed_at: timestamp,
      last_error_code: 'PermanentFailure',
    }] } })
  })

  await page.goto('/integrations')
  await expect(page.getByRole('button', { name: '创建身份映射' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '创建路由映射' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '重放' })).toBeDisabled()
})
