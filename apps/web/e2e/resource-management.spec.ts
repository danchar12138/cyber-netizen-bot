import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const blankAgentId = '55555555-5555-4555-8555-555555555555'
const copiedAgentId = '66666666-6666-4666-8666-666666666666'
const timestamp = '2026-09-09T08:00:00Z'

test('可以创建、复制、切换并批量停用 Agent', async ({ page }) => {
  let agentStatus: 'active' | 'disabled' = 'active'
  const agents = [{
    id: agentId,
    tenant_id: tenantId,
    name: '赛博网友',
    status: agentStatus,
    created_at: timestamp,
  }]
  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({
      json: {
        tenant_id: tenantId,
        user_id: userId,
        display_name: '本地开发者',
        role: 'admin',
        permissions: ['agent:read', 'agent:write', 'audit:read'],
        authentication_mode: 'development',
      },
    })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    await route.fulfill({
      json: {
        items: agents.map((agent) => ({ ...agent, status: agent.id === agentId ? agentStatus : agent.status })),
        next_cursor: null,
      },
    })
  })
  await page.route('**/api/v1/administration/agents', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const command = route.request().postDataJSON() as { name: string }
    expect(command.name).toBe('安静伙伴')
    const created = {
      id: blankAgentId,
      tenant_id: tenantId,
      name: command.name,
      status: 'active' as const,
      created_at: timestamp,
    }
    agents.push(created)
    await route.fulfill({ status: 201, json: created })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/copy`, async (route) => {
    const command = route.request().postDataJSON() as { name: string }
    expect(command.name).toBe('赛博网友副本')
    const copied = {
      id: copiedAgentId,
      tenant_id: tenantId,
      name: command.name,
      status: 'active' as const,
      created_at: timestamp,
    }
    agents.push(copied)
    await route.fulfill({ status: 201, json: copied })
  })
  await page.route('**/api/v1/administration/agents/status', async (route) => {
    const command = route.request().postDataJSON() as {
      ids: string[]
      status: 'active' | 'disabled'
      confirmed: boolean
    }
    expect(command).toEqual({ ids: [agentId], status: 'disabled', confirmed: true })
    agentStatus = command.status
    await route.fulfill({
      json: {
        items: [{
          id: agentId,
          tenant_id: tenantId,
          name: '赛博网友',
          status: agentStatus,
          created_at: timestamp,
        }],
        next_cursor: null,
      },
    })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/impact`, async (route) => {
    await route.fulfill({ json: {
      agent: {
        id: agentId, tenant_id: tenantId, name: '赛博网友', status: agentStatus,
        created_at: timestamp, archived_at: null, deleted_at: null, purge_after: null,
      },
      counts: {
        conversations: 0, agent_runs: 0, cognition_resource_versions: 0, memories: 0,
        relationships: 0, evaluation_suites: 0, evaluation_runs: 0,
        channel_instances: 0, scheduled_actions: 0, total: 0,
      },
      active_replacement_count: 2,
      can_archive: true,
      can_delete: false,
      blockers: ['只有已归档的 Agent 可以进入软删除保留期'],
      archive_confirmation: `确认归档 Agent ${agentId}`,
      delete_confirmation: `确认删除 Agent ${agentId}`,
      deleted_agent_retention_days: 30,
    } })
  })
  await page.route('**/api/v1/administration/audit?*', async (route) => {
    await route.fulfill({
      json: {
        items: [{
          id: 1,
          actor_id: userId,
          action: 'agent.status_updated',
          resource_type: 'agent',
          resource_id: null,
          detail: { ids: [agentId], status: 'disabled' },
          created_at: timestamp,
        }],
        next_cursor: null,
      },
    })
  })

  await page.goto('/agents')
  await page.getByLabel('新 Agent 名称').fill('安静伙伴')
  await page.getByRole('button', { name: '创建并切换' }).click()
  await expect(page.getByLabel('当前 Agent')).toHaveValue(blankAgentId)
  await expect(page.getByRole('table').getByText('安静伙伴', { exact: true })).toBeVisible()

  await page.getByLabel('源 Agent').selectOption(agentId)
  await page.getByLabel('复制后的 Agent 名称').fill('赛博网友副本')
  await page.getByRole('button', { name: '复制并切换' }).click()
  await expect(page.getByLabel('当前 Agent')).toHaveValue(copiedAgentId)
  await expect(page.getByRole('table').getByText('赛博网友副本', { exact: true })).toBeVisible()

  await page.getByRole('checkbox', { name: `选择 赛博网友 ${agentId} active` }).check()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '批量停用' }).click()
  await expect(page.getByText('已停用')).toBeVisible()

  await page.goto('/audit')
  await expect(page.getByText('更新 Agent 状态')).toBeVisible()
  await expect(page.getByText(/状态：已停用/)).toBeVisible()
})

test('可以预览影响后重命名、归档并将 Agent 置入软删除保留期', async ({ page }) => {
  let name = '待归档伙伴'
  let lifecycleStatus: 'active' | 'archived' | 'deleted' = 'active'
  let archivedAt: string | null = null
  let deletedAt: string | null = null
  let purgeAfter: string | null = null
  const replacementId = '77777777-7777-4777-8777-777777777777'

  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '本地开发者',
      role: 'admin',
      permissions: ['agent:read', 'agent:write'],
      authentication_mode: 'development',
    } })
  })
  await page.route('**/api/v1/administration/agents?*', async (route) => {
    const activeOnly = new URL(route.request().url()).searchParams.get('entity_status') === 'active'
    const items = [
      {
        id: agentId, tenant_id: tenantId, name, status: lifecycleStatus,
        created_at: timestamp, archived_at: archivedAt, deleted_at: deletedAt, purge_after: purgeAfter,
      },
      {
        id: replacementId, tenant_id: tenantId, name: '替代伙伴', status: 'active',
        created_at: timestamp, archived_at: null, deleted_at: null, purge_after: null,
      },
    ].filter((agent) => !activeOnly || agent.status === 'active')
    await route.fulfill({ json: { items, next_cursor: null } })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}`, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    const command = route.request().postDataJSON() as { name: string }
    name = command.name
    await route.fulfill({ json: {
      id: agentId, tenant_id: tenantId, name, status: lifecycleStatus,
      created_at: timestamp, archived_at: archivedAt, deleted_at: deletedAt, purge_after: purgeAfter,
    } })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/impact`, async (route) => {
    await route.fulfill({ json: {
      agent: {
        id: agentId, tenant_id: tenantId, name, status: lifecycleStatus,
        created_at: timestamp, archived_at: archivedAt, deleted_at: deletedAt, purge_after: purgeAfter,
      },
      counts: {
        conversations: 12, agent_runs: 18, cognition_resource_versions: 6, memories: 32,
        relationships: 4, evaluation_suites: 2, evaluation_runs: 9,
        channel_instances: 3, scheduled_actions: 5, total: 91,
      },
      active_replacement_count: 1,
      can_archive: lifecycleStatus === 'active',
      can_delete: lifecycleStatus === 'archived',
      blockers: lifecycleStatus === 'active'
        ? ['只有已归档的 Agent 可以进入软删除保留期']
        : lifecycleStatus === 'archived'
          ? ['只有已启用或已停用的 Agent 可以归档']
          : ['只有已启用或已停用的 Agent 可以归档', '只有已归档的 Agent 可以进入软删除保留期'],
      archive_confirmation: `确认归档 Agent ${agentId}`,
      delete_confirmation: `确认删除 Agent ${agentId}`,
      deleted_agent_retention_days: 30,
    } })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/archive`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmation: `确认归档 Agent ${agentId}` })
    lifecycleStatus = 'archived'
    archivedAt = '2026-09-10T08:00:00Z'
    await route.fulfill({ json: {
      id: agentId, tenant_id: tenantId, name, status: lifecycleStatus,
      created_at: timestamp, archived_at: archivedAt, deleted_at: null, purge_after: null,
    } })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/delete`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmation: `确认删除 Agent ${agentId}` })
    lifecycleStatus = 'deleted'
    deletedAt = '2026-09-10T08:01:00Z'
    purgeAfter = '2026-10-10T08:01:00Z'
    await route.fulfill({ json: {
      id: agentId, tenant_id: tenantId, name, status: lifecycleStatus,
      created_at: timestamp, archived_at: archivedAt, deleted_at: deletedAt, purge_after: purgeAfter,
    } })
  })

  await page.goto('/agents')
  await page.getByRole('checkbox', { name: `选择 待归档伙伴 ${agentId} active` }).check()
  await expect(page.getByLabel('关联资源影响统计')).toContainText('91')
  await expect(page.getByText('其他已启用 Agent：1 个')).toBeVisible()

  await page.getByLabel('Agent 名称', { exact: true }).fill('长期伙伴')
  await page.getByRole('button', { name: '保存名称' }).click()
  await expect(page.getByRole('table').getByText('长期伙伴', { exact: true })).toBeVisible()

  await page.getByLabel('Agent 生命周期确认短语').fill(`确认归档 Agent ${agentId}`)
  await page.getByRole('button', { name: '确认归档' }).click()
  await expect(page.getByRole('table').getByText('已归档')).toBeVisible()

  await page.getByLabel('Agent 生命周期确认短语').fill(`确认删除 Agent ${agentId}`)
  await page.getByRole('button', { name: '进入软删除保留期' }).click()
  await expect(page.getByRole('table').getByText('等待清理')).toBeVisible()
  await expect(page.getByText(/最早物理清理时间/)).toBeVisible()
})
