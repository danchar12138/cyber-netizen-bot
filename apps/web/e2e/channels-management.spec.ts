import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const otherAgentId = '77777777-7777-4777-8777-777777777777'
const channelId = '55555555-5555-4555-8555-555555555555'
const telegramChannelId = '88888888-8888-4888-8888-888888888888'
const timestamp = '2026-09-10T08:00:00Z'

const webCapabilities = {
  text: true, markdown: true, images: true, files: true, streaming: true,
  reactions: true, threads: true, message_edit: true, proactive_messages: true,
  max_text_chars: 20000, max_blocks: 20, max_attachment_bytes: 262144000,
  accepted_content_types: ['image/png', 'application/pdf'],
}

test('可以管理渠道、运行能力协商并查看安全诊断', async ({ page }) => {
  const scopedRequestAgents: Array<string | undefined> = []
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId, user_id: userId, display_name: '本地开发者', role: 'admin',
      permissions: ['agent:read', 'channel:read', 'channel:write', 'channel:send', 'channel_credential:manage'],
      authentication_mode: 'development',
    } })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    await route.fulfill({ json: { items: [
      { id: agentId, tenant_id: tenantId, name: '赛博网友', status: 'active', created_at: timestamp },
      { id: otherAgentId, tenant_id: tenantId, name: '安静伙伴', status: 'active', created_at: timestamp },
    ], next_cursor: null } })
  })
  await page.route('**/api/v1/channels/catalog', async (route) => {
    await route.fulfill({ json: { items: [
      { platform: 'web', display_name: '内部 Web', implementation_status: 'ready', credential_required: false, capabilities: webCapabilities },
      { platform: 'feishu', display_name: '飞书', implementation_status: 'placeholder', credential_required: true, capabilities: { ...webCapabilities, streaming: false, max_text_chars: 30000 } },
      { platform: 'discord', display_name: 'Discord', implementation_status: 'placeholder', credential_required: true, capabilities: { ...webCapabilities, streaming: false, max_text_chars: 2000 } },
      { platform: 'telegram', display_name: 'Telegram', implementation_status: 'ready', credential_required: true, capabilities: { ...webCapabilities, markdown: false, images: false, files: false, streaming: false, reactions: false, max_text_chars: 4096, max_blocks: 1, accepted_content_types: [] } },
    ] } })
  })
  await page.route('**/api/v1/channels/model-capabilities', async (route) => {
    await route.fulfill({ json: { items: [
      { provider: 'development', model_family: 'friendly-echo-v1', text_input: true, image_input: false, document_input: false, streaming: true, structured_output: false, tool_calling: false },
      { provider: 'openai', model_family: 'responses-api', text_input: true, image_input: true, document_input: true, streaming: true, structured_output: true, tool_calling: true },
    ] } })
  })
  const instance = {
    id: channelId, tenant_id: tenantId, agent_id: agentId, name: '内部 Web', platform: 'web', display_name: '内部 Web',
    implementation_status: 'ready', status: 'enabled', rate_limit_per_minute: 60,
    settings: { audience: 'internal' }, credential_configured: true, capabilities: webCapabilities,
    health_status: 'healthy', health_detail: '内部 Web 适配器已就绪，无需外部凭证。',
    last_checked_at: timestamp, created_by: userId, created_at: timestamp, updated_at: timestamp,
  }
  const telegramInstance = {
    ...instance,
    id: telegramChannelId,
    name: 'Telegram 出站',
    platform: 'telegram',
    display_name: 'Telegram',
    credential_configured: true,
    capabilities: { ...webCapabilities, markdown: false, images: false, files: false, streaming: false, reactions: false, max_text_chars: 4096, max_blocks: 1, accepted_content_types: [] },
    health_detail: 'Telegram Bot API 连接正常，凭证已验证。',
  }
  await page.route('**/api/v1/channels', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedRequestAgents.push(selectedAgent)
    await route.fulfill({ json: { items: selectedAgent === otherAgentId ? [] : [instance, telegramInstance] } })
  })
  await page.route('**/api/v1/channels/diagnostics/events?*', async (route) => {
    const selectedAgent = route.request().headers()['x-cnb-agent-id']
    scopedRequestAgents.push(selectedAgent)
    await route.fulfill({ json: { items: selectedAgent === otherAgentId ? [] : [{
      id: '66666666-6666-4666-8666-666666666666', channel_id: channelId,
      direction: 'system', event_type: 'connection.tested', status: 'delivered',
      external_event_id: null, idempotency_key: 'connection-test:1', external_message_id: null,
      payload_summary: { health_status: 'healthy' }, error_code: null, degradations: [], occurred_at: timestamp,
    }] } })
  })
  await page.route('**/api/v1/channels/simulate', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ platform: 'discord', request_streaming: true })
    await route.fulfill({ json: {
      platform: 'discord',
      blocks: [{ kind: 'markdown', text: '能力协商测试', attachment_id: null, content_type: null, file_name: null, size_bytes: null, sha256: null, alt_text: null }],
      degradations: ['streaming_to_buffered'], buffered: true, thread_preserved: true, edit_preserved: true,
    } })
  })
  await page.route(`**/api/v1/channels/${channelId}/connection-test`, async (route) => {
    scopedRequestAgents.push(route.request().headers()['x-cnb-agent-id'])
    await route.fulfill({ json: instance })
  })
  await page.route(`**/api/v1/channels/${telegramChannelId}/deliveries`, async (route) => {
    scopedRequestAgents.push(route.request().headers()['x-cnb-agent-id'])
    expect(route.request().postDataJSON()).toMatchObject({
      recipient_id: '-1001234567890',
      blocks: [{ kind: 'text', text: '真实出站联调' }],
      thread_id: '17',
      edit_message_id: null,
      proactive: true,
    })
    await route.fulfill({ json: {
      status: 'delivered', external_message_id: '88', degradations: [],
      delivered_at: timestamp, idempotent_replay: false,
    } })
  })

  await page.goto('/channels')
  await expect(page.getByRole('heading', { name: '渠道与适配器' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '平台能力模拟器' })).toBeVisible()
  await expect(page.getByText('凭证只写入信封加密存储')).toBeVisible()
  await expect(page.getByText('Telegram 已接通安全文本入站')).toBeVisible()
  await expect(page.getByRole('heading', { name: '出站消息联调' })).toBeVisible()
  await expect(page.getByText(`当前标识：${agentId}。`)).toBeVisible()
  await expect(page.getByText('流式响应', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '运行能力协商' }).click()
  await expect(page.getByText('发生透明降级')).toBeVisible()
  await expect(page.getByText('流式响应改为缓冲后发送')).toBeVisible()
  await page.getByLabel('启用的正式渠道').selectOption(telegramChannelId)
  await page.getByLabel('收件人 / chat ID').fill('-1001234567890')
  await page.getByLabel('话题 ID（可选）').fill('17')
  await page.getByLabel('纯文本消息').fill('真实出站联调')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '确认发送' }).click()
  await expect(page.getByText(/已送达 · 88 · 无降级/)).toBeVisible()
  await page.getByRole('button', { name: '连接测试' }).first().click()
  await expect(page.getByText('连接测试', { exact: true }).last()).toBeVisible()
  await expect(page.getByText(/系统 · 66666666/)).toBeVisible()
  await page.getByLabel('当前智能体').selectOption(otherAgentId)
  await expect(page.getByText(`当前标识：${otherAgentId}。`)).toBeVisible()
  await expect(page.getByText('尚未创建渠道实例')).toBeVisible()
  expect(scopedRequestAgents).toContain(agentId)
  expect(scopedRequestAgents).toContain(otherAgentId)
  expect(
    scopedRequestAgents
      .filter((value) => value !== undefined)
      .every((value) => value === agentId || value === otherAgentId),
  ).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
