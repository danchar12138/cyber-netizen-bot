import { expect, test } from './fixtures'

const tenantId = '44444444-4444-4444-8444-444444444444'
const userId = '22222222-2222-4222-8222-222222222222'
const agentId = '33333333-3333-4333-8333-333333333333'
const blankAgentId = '55555555-5555-4555-8555-555555555555'
const copiedAgentId = '66666666-6666-4666-8666-666666666666'
const timestamp = '2026-09-09T08:00:00Z'

test('可以创建、复制、切换并批量停用智能体', async ({ page }) => {
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
      blockers: ['只有已归档的智能体可以进入软删除保留期'],
      archive_confirmation: `确认归档智能体 ${agentId}`,
      delete_confirmation: `确认删除智能体 ${agentId}`,
      deleted_agent_retention_days: 30,
    } })
  })
  await page.route('**/api/v1/administration/audit?*', async (route) => {
    const resourceId = new URL(route.request().url()).searchParams.get('resource_id')
    const items = [{
      id: 2,
      actor_id: userId,
      action: 'observability.alert_calibration_draft_created',
      resource_type: 'configuration_version',
      resource_id: '88888888-8888-4888-8888-888888888888',
      detail: {
        configuration_version: 7,
        replay_window_started_at: '2026-09-08T08:00:00Z',
        replay_window_ended_at: timestamp,
        replay_fingerprint: 'a'.repeat(64),
        proposal_count: 1,
        eligible_feedback: 25,
      },
      created_at: timestamp,
    }, {
      id: 1,
      actor_id: userId,
      action: 'agent.status_updated',
      resource_type: 'agent',
      resource_id: null,
      detail: { ids: [agentId], status: 'disabled' },
      created_at: timestamp,
    }].filter((item) => !resourceId || item.resource_id === resourceId)
    await route.fulfill({
      json: {
        items,
        next_cursor: null,
      },
    })
  })

  await page.goto('/agents')
  await page.getByLabel('新智能体名称').fill('安静伙伴')
  await page.getByRole('button', { name: '创建并切换' }).click()
  await expect(page.getByLabel('当前智能体')).toHaveValue(blankAgentId)
  await expect(page.getByRole('table').getByText('安静伙伴', { exact: true })).toBeVisible()

  await page.getByLabel('源智能体').selectOption(agentId)
  await page.getByLabel('复制后的智能体名称').fill('赛博网友副本')
  await page.getByRole('button', { name: '复制并切换' }).click()
  await expect(page.getByLabel('当前智能体')).toHaveValue(copiedAgentId)
  await expect(page.getByRole('table').getByText('赛博网友副本', { exact: true })).toBeVisible()

  await page.getByRole('checkbox', { name: `选择 赛博网友 ${agentId} active` }).check()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '批量停用' }).click()
  await expect(page.getByText('已停用')).toBeVisible()

  await page.goto('/audit')
  await expect(page.getByText('更新智能体状态')).toBeVisible()
  await expect(page.getByText('创建告警校准草稿')).toBeVisible()
  await expect(page.getByText(/状态：已停用/)).toBeVisible()
  await page.getByPlaceholder('精确匹配资源 ID').fill('88888888-8888-4888-8888-888888888888')
  await page.getByRole('button', { name: '查询' }).click()
  await expect(page.getByText('创建告警校准草稿')).toBeVisible()
  await expect(page.getByText('更新智能体状态')).not.toBeVisible()
})

test('可以预览影响后重命名、归档并将智能体置入软删除保留期', async ({ page }) => {
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
        ? ['只有已归档的智能体可以进入软删除保留期']
        : lifecycleStatus === 'archived'
          ? ['只有已启用或已停用的智能体可以归档']
          : ['只有已启用或已停用的智能体可以归档', '只有已归档的智能体可以进入软删除保留期'],
      archive_confirmation: `确认归档智能体 ${agentId}`,
      delete_confirmation: `确认删除智能体 ${agentId}`,
      deleted_agent_retention_days: 30,
    } })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/archive`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmation: `确认归档智能体 ${agentId}` })
    lifecycleStatus = 'archived'
    archivedAt = '2026-09-10T08:00:00Z'
    await route.fulfill({ json: {
      id: agentId, tenant_id: tenantId, name, status: lifecycleStatus,
      created_at: timestamp, archived_at: archivedAt, deleted_at: null, purge_after: null,
    } })
  })
  await page.route(`**/api/v1/administration/agents/${agentId}/delete`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmation: `确认删除智能体 ${agentId}` })
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
  await expect(page.getByText('其他已启用智能体：1 个')).toBeVisible()

  await page.getByLabel('智能体名称', { exact: true }).fill('长期伙伴')
  await page.getByRole('button', { name: '保存名称' }).click()
  await expect(page.getByRole('table').getByText('长期伙伴', { exact: true })).toBeVisible()

  await page.getByLabel('智能体生命周期确认短语').fill(`确认归档智能体 ${agentId}`)
  await page.getByRole('button', { name: '确认归档' }).click()
  await expect(page.getByRole('table').getByText('已归档')).toBeVisible()

  await page.getByLabel('智能体生命周期确认短语').fill(`确认删除智能体 ${agentId}`)
  await page.getByRole('button', { name: '进入软删除保留期' }).click()
  await expect(page.getByRole('table').getByText('等待清理')).toBeVisible()
  await expect(page.getByText(/最早物理清理时间/)).toBeVisible()
})

test('可以查看用户完整身份治理详情并逐字确认撤销管理会话', async ({ page }) => {
  const sessionId = '88888888-8888-4888-8888-888888888888'
  const identityId = '99999999-9999-4999-8999-999999999999'
  let revokedAt: string | null = null
  let requestRateLimit: number | null = null
  let suspendedUntil: string | null = null
  let suspensionReason: string | null = null
  let role: 'admin' | 'operator' = 'admin'
  let roleSource: 'oidc' | 'manual' = 'oidc'
  let overriddenBy: string | null = null
  let overrideExpiresAt: string | null = null

  await page.route('**/api/v1/administration/session', async (route) => {
    await route.fulfill({ json: {
      tenant_id: tenantId,
      user_id: userId,
      display_name: '身份治理管理员',
      role: 'admin',
      permissions: ['user:read', 'user:write', 'user:role_write', 'audit:read'],
      authentication_mode: 'oidc',
    } })
  })
  await page.route('**/api/v1/administration/users?*', async (route) => {
    await route.fulfill({ json: {
      items: [{
        id: userId,
        tenant_id: tenantId,
        display_name: '身份治理管理员',
        status: 'active',
        created_at: timestamp,
      }],
      next_cursor: null,
    } })
  })
  await page.route(`**/api/v1/administration/users/${userId}`, async (route) => {
    await route.fulfill({ json: {
      user: {
        id: userId,
        tenant_id: tenantId,
        display_name: '身份治理管理员',
        status: 'active',
        created_at: timestamp,
      },
      tenant: { id: tenantId, name: '人格实验室', status: 'active', created_at: timestamp },
      role_assignment: {
        role,
        source: roleSource,
        trusted_role: 'admin',
        overridden_by: overriddenBy,
        override_expires_at: overrideExpiresAt,
        created_at: timestamp,
        updated_at: timestamp,
      },
      access_policy: {
        request_rate_limit_per_minute: requestRateLimit,
        suspended_until: suspendedUntil,
        suspension_reason: suspensionReason,
        updated_at: requestRateLimit === null && suspendedUntil === null ? null : timestamp,
      },
      external_identities: [{
        id: identityId,
        issuer: 'https://identity.example.test/realms/cnb',
        subject: 'admin-subject',
        created_at: timestamp,
        last_authenticated_at: timestamp,
      }],
      admin_sessions: [{
        id: sessionId,
        external_identity_id: identityId,
        issued_at: timestamp,
        expires_at: '2099-09-10T12:00:00Z',
        last_seen_at: timestamp,
        revoked_at: revokedAt,
      }],
      conversation_memberships: [{
        conversation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        agent_id: agentId,
        title: '共同打磨人格',
        role: 'owner',
        status: 'active',
        joined_at: timestamp,
        deleted_at: null,
      }],
    } })
  })
  await page.route(`**/api/v1/administration/users/${userId}/access-policy`, async (route) => {
    const command = route.request().postDataJSON() as {
      request_rate_limit_per_minute: number | null
      suspended_until: string | null
      suspension_reason: string | null
      confirmation: string
    }
    expect(command.confirmation).toBe(`确认更新用户访问策略 ${userId}`)
    requestRateLimit = command.request_rate_limit_per_minute
    suspendedUntil = command.suspended_until
    suspensionReason = command.suspension_reason
    await route.fulfill({ json: {
      request_rate_limit_per_minute: requestRateLimit,
      suspended_until: suspendedUntil,
      suspension_reason: suspensionReason,
      updated_at: timestamp,
    } })
  })
  await page.route(`**/api/v1/administration/users/${userId}/role-override`, async (route) => {
    const command = route.request().postDataJSON() as {
      role: 'admin' | 'operator'
      override_expires_at: string | null
      confirmation: string
    }
    expect(command.confirmation).toBe(`确认覆盖用户角色 ${userId}`)
    role = command.role
    roleSource = 'manual'
    overriddenBy = userId
    overrideExpiresAt = command.override_expires_at
    await route.fulfill({ json: {
      role,
      source: roleSource,
      trusted_role: 'admin',
      overridden_by: overriddenBy,
      override_expires_at: overrideExpiresAt,
      created_at: timestamp,
      updated_at: timestamp,
    } })
  })
  await page.route(`**/api/v1/administration/users/${userId}/role-override/revoke`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      confirmation: `确认撤销用户角色覆盖 ${userId}`,
    })
    role = 'admin'
    roleSource = 'oidc'
    overriddenBy = null
    overrideExpiresAt = null
    await route.fulfill({ json: {
      role,
      source: roleSource,
      trusted_role: 'admin',
      overridden_by: null,
      override_expires_at: null,
      created_at: timestamp,
      updated_at: timestamp,
    } })
  })
  await page.route(
    `**/api/v1/administration/users/${userId}/sessions/${sessionId}/revoke`,
    async (route) => {
      expect(route.request().postDataJSON()).toEqual({
        confirmation: `确认撤销管理会话 ${sessionId}`,
      })
      revokedAt = '2026-09-10T12:00:00Z'
      await route.fulfill({ json: {
        id: sessionId,
        external_identity_id: identityId,
        issued_at: timestamp,
        expires_at: '2099-09-10T12:00:00Z',
        last_seen_at: timestamp,
        revoked_at: revokedAt,
      } })
    },
  )

  await page.goto('/users')
  await page.getByRole('checkbox', { name: `选择 身份治理管理员 ${userId} active` }).check()
  await expect(page.getByRole('region', { name: '用户身份治理' })).toContainText('人格实验室')
  await expect(page.getByText('OIDC 可信声明同步').first()).toBeVisible()
  await expect(page.getByText('https://identity.example.test/realms/cnb')).toBeVisible()
  await expect(page.getByText('共同打磨人格')).toBeVisible()
  await expect(page.getByText(/如果撤销的会话正被本页面使用/)).toBeVisible()

  await page.getByLabel('用户每分钟请求上限').fill('30')
  await page.getByLabel('用户临时停用时间').fill('2099-09-10T12:00')
  await page.getByLabel('用户临时停用原因').fill('安全演练')
  await expect(page.getByText(/保存临时停用后，本页面会立即失去访问权限/)).toBeVisible()
  await page.getByLabel('用户访问策略确认短语').fill(`确认更新用户访问策略 ${userId}`)
  await page.getByRole('button', { name: '保存访问策略' }).click()
  await expect(page.getByText('每分钟 30 次').first()).toBeVisible()
  await expect(page.getByText('安全演练')).toBeVisible()

  await page.getByLabel('用户覆盖角色').selectOption('operator')
  await expect(page.getByText('自我降权会立即生效')).toBeVisible()
  await page.getByLabel('用户角色覆盖确认短语').fill(`确认覆盖用户角色 ${userId}`)
  await page.getByRole('button', { name: '保存角色覆盖' }).click()
  await expect(page.getByText('管理员手工覆盖').first()).toBeVisible()
  await page.getByLabel('撤销用户角色覆盖确认短语').fill(`确认撤销用户角色覆盖 ${userId}`)
  await page.getByRole('button', { name: '撤销角色覆盖' }).click()
  await expect(page.getByText('OIDC 可信声明同步').first()).toBeVisible()

  await page.getByRole('button', { name: '撤销', exact: true }).click()
  const confirmation = page.getByLabel('管理会话撤销确认短语')
  await confirmation.fill('确认撤销')
  await expect(page.getByRole('button', { name: '确认撤销' })).toBeDisabled()
  await confirmation.fill(`确认撤销管理会话 ${sessionId}`)
  await page.getByRole('button', { name: '确认撤销' }).click()
  await expect(page.getByText(/已撤销/)).toBeVisible()
  await expect(page.getByRole('button', { name: '撤销', exact: true })).not.toBeVisible()
})
