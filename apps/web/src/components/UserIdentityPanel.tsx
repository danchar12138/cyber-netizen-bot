import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Clock3,
  Gauge,
  KeyRound,
  Link2,
  LogOut,
  MessagesSquare,
  ShieldAlert,
  ShieldCheck,
  UserCog,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  type AdminRole,
  getManagedUserDetail,
  revokeManagedUserRoleOverride,
  revokeManagedUserSession,
  setManagedUserRoleOverride,
  updateManagedUserAccessPolicy,
} from '../api'
import { adminRoleLabels } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN')

const toLocalDateTimeInput = (value: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16)
}

const toIsoTime = (value: string) => value ? new Date(value).toISOString() : null

const roleSourceLabel = (source: 'oidc' | 'development' | 'manual') => {
  if (source === 'development') return '本地开发环境'
  if (source === 'manual') return '管理员手工覆盖'
  return 'OIDC 可信声明同步'
}

export function UserIdentityPanel({
  userId,
  currentUserId,
  canWrite,
  canManageRole,
}: {
  userId: string
  currentUserId?: string
  canWrite: boolean
  canManageRole: boolean
}) {
  const queryClient = useQueryClient()
  const [confirmation, setConfirmation] = useState('')
  const [targetSessionId, setTargetSessionId] = useState<string | null>(null)
  const [rateLimit, setRateLimit] = useState('')
  const [suspendedUntil, setSuspendedUntil] = useState('')
  const [suspensionReason, setSuspensionReason] = useState('')
  const [policyConfirmation, setPolicyConfirmation] = useState('')
  const [overrideRole, setOverrideRole] = useState<AdminRole>('viewer')
  const [overrideExpiresAt, setOverrideExpiresAt] = useState('')
  const [roleConfirmation, setRoleConfirmation] = useState('')
  const [revokeRoleConfirmation, setRevokeRoleConfirmation] = useState('')
  const detail = useQuery({
    queryKey: ['managed-user-detail', userId],
    queryFn: () => getManagedUserDetail(userId),
  })

  useEffect(() => {
    if (!detail.data) return
    const activeSuspension = detail.data.access_policy.suspended_until
      && new Date(detail.data.access_policy.suspended_until).getTime() > Date.now()
      ? detail.data.access_policy.suspended_until
      : null
    const activeRoleExpiry = detail.data.role_assignment?.override_expires_at
      && new Date(detail.data.role_assignment.override_expires_at).getTime() > Date.now()
      ? detail.data.role_assignment.override_expires_at
      : null
    setRateLimit(detail.data.access_policy.request_rate_limit_per_minute?.toString() ?? '')
    setSuspendedUntil(toLocalDateTimeInput(activeSuspension))
    setSuspensionReason(activeSuspension ? detail.data.access_policy.suspension_reason ?? '' : '')
    setOverrideRole(detail.data.role_assignment?.role ?? 'viewer')
    setOverrideExpiresAt(toLocalDateTimeInput(activeRoleExpiry))
  }, [detail.data])

  const refreshGovernance = async (includeSession = false) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['managed-user-detail', userId] }),
      invalidateAcrossTabs(queryClient, ['audit-records']),
      ...(includeSession && currentUserId === userId
        ? [invalidateAcrossTabs(queryClient, ['admin-session'])]
        : []),
    ])
  }
  const revoke = useMutation({
    mutationFn: ({ sessionId }: { sessionId: string }) =>
      revokeManagedUserSession(userId, sessionId, confirmation),
    onSuccess: async () => {
      setConfirmation('')
      setTargetSessionId(null)
      await refreshGovernance(true)
    },
  })
  const updatePolicy = useMutation({
    mutationFn: () => updateManagedUserAccessPolicy(userId, {
      request_rate_limit_per_minute: rateLimit ? Number(rateLimit) : null,
      suspended_until: toIsoTime(suspendedUntil),
      suspension_reason: suspendedUntil ? suspensionReason.trim() : null,
      confirmation: policyConfirmation,
    }),
    onSuccess: async () => {
      setPolicyConfirmation('')
      await refreshGovernance()
    },
  })
  const setRoleOverride = useMutation({
    mutationFn: () => setManagedUserRoleOverride(userId, {
      role: overrideRole,
      override_expires_at: toIsoTime(overrideExpiresAt),
      confirmation: roleConfirmation,
    }),
    onSuccess: async () => {
      setRoleConfirmation('')
      await refreshGovernance(true)
    },
  })
  const revokeRoleOverride = useMutation({
    mutationFn: () => revokeManagedUserRoleOverride(userId, revokeRoleConfirmation),
    onSuccess: async () => {
      setRevokeRoleConfirmation('')
      await refreshGovernance(true)
    },
  })

  if (detail.isLoading) {
    return <section className="panel user-identity-panel">正在聚合身份与会话信息…</section>
  }
  if (detail.error || !detail.data) {
    return <div className="notice error" role="alert">{detail.error?.message ?? '用户详情读取失败'}</div>
  }

  const data = detail.data
  const now = Date.now()
  const isCurrentUser = currentUserId === userId
  const policyConfirmationText = `确认更新用户访问策略 ${userId}`
  const roleConfirmationText = `确认覆盖用户角色 ${userId}`
  const revokeRoleConfirmationText = `确认撤销用户角色覆盖 ${userId}`
  const selectedConfirmation = targetSessionId
    ? `确认撤销管理会话 ${targetSessionId}`
    : ''
  const isSuspended = data.access_policy.suspended_until !== null
    && new Date(data.access_policy.suspended_until).getTime() > now

  return (
    <section className="panel user-identity-panel" aria-label="用户身份治理">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">身份与访问治理详情</p>
          <h2>{data.user.display_name}</h2>
        </div>
        <span className={`entity-status ${data.user.status}`}>
          {data.user.status === 'active' ? '已启用' : '已停用'}
        </span>
      </div>

      <div className="identity-summary-grid">
        <article>
          <ShieldCheck size={16} />
          <span>当前租户</span>
          <strong>{data.tenant.name}</strong>
          <code>{data.tenant.id}</code>
        </article>
        <article>
          <KeyRound size={16} />
          <span>当前生效角色</span>
          <strong>{data.role_assignment ? adminRoleLabels[data.role_assignment.role] : '未分配'}</strong>
          <small>{data.role_assignment ? roleSourceLabel(data.role_assignment.source) : '没有角色来源'}</small>
        </article>
        <article>
          <Gauge size={16} />
          <span>访问策略</span>
          <strong>{isSuspended ? '临时停用中' : data.access_policy.request_rate_limit_per_minute ? `每分钟 ${data.access_policy.request_rate_limit_per_minute} 次` : '不限流'}</strong>
          <small>{data.access_policy.updated_at ? `更新于 ${formatTime(data.access_policy.updated_at)}` : '尚未单独配置'}</small>
        </article>
        <article>
          <Link2 size={16} />
          <span>OIDC 绑定</span>
          <strong>{data.external_identities.length} 个</strong>
          <small>{data.external_identities.length === 0 ? '没有伪造外部身份' : '仅展示已验证主体'}</small>
        </article>
      </div>

      <div className="identity-section">
        <h3><Gauge size={14} /> 访问策略</h3>
        <div className="identity-policy-grid">
          <div><span>请求预算</span><strong>{data.access_policy.request_rate_limit_per_minute ? `每分钟 ${data.access_policy.request_rate_limit_per_minute} 次` : '未限制'}</strong></div>
          <div><span>临时停用</span><strong>{isSuspended && data.access_policy.suspended_until ? `至 ${formatTime(data.access_policy.suspended_until)}` : '未生效'}</strong></div>
          <div><span>停用原因</span><strong>{data.access_policy.suspension_reason ?? '—'}</strong></div>
        </div>
        {canWrite && (
          <form className="user-governance-form" onSubmit={(event) => {
            event.preventDefault()
            updatePolicy.mutate()
          }}>
            <label>
              <span>每分钟请求上限</span>
              <input
                aria-label="用户每分钟请求上限"
                type="number"
                min={1}
                max={10_000}
                placeholder="留空表示不限流"
                value={rateLimit}
                onChange={(event) => setRateLimit(event.target.value)}
              />
            </label>
            <label>
              <span>临时停用至</span>
              <input
                aria-label="用户临时停用时间"
                type="datetime-local"
                value={suspendedUntil}
                onChange={(event) => setSuspendedUntil(event.target.value)}
              />
            </label>
            <label className="governance-reason-field">
              <span>停用原因</span>
              <input
                aria-label="用户临时停用原因"
                maxLength={500}
                placeholder={suspendedUntil ? '临时停用时必填' : '设置停用时间后填写'}
                value={suspensionReason}
                onChange={(event) => setSuspensionReason(event.target.value)}
              />
            </label>
            {isCurrentUser && (suspendedUntil || (rateLimit && Number(rateLimit) <= 10)) && (
              <div className="notice warning governance-warning">
                <ShieldAlert size={16} />
                <div><strong>当前正在修改自己的访问能力</strong><span>{suspendedUntil ? '保存临时停用后，本页面会立即失去访问权限。' : '较低的请求预算可能让管理后台在一分钟内暂时不可用。'}</span></div>
              </div>
            )}
            <div className="governance-confirmation">
              <label htmlFor={`policy-confirmation-${userId}`}>逐字输入 <code>{policyConfirmationText}</code></label>
              <input
                id={`policy-confirmation-${userId}`}
                aria-label="用户访问策略确认短语"
                value={policyConfirmation}
                onChange={(event) => setPolicyConfirmation(event.target.value)}
              />
              <button
                className="primary-button"
                disabled={policyConfirmation !== policyConfirmationText || Boolean(suspendedUntil && !suspensionReason.trim()) || updatePolicy.isPending}
              >保存访问策略</button>
            </div>
          </form>
        )}
        {updatePolicy.error && <div className="notice error" role="alert">{updatePolicy.error.message}</div>}
      </div>

      <div className="identity-section">
        <h3><UserCog size={14} /> 可信角色与手工覆盖</h3>
        {data.role_assignment === null ? (
          <p className="empty-inline">当前用户尚无可信角色基线，不能创建手工覆盖。</p>
        ) : (
          <>
            <div className="identity-policy-grid">
              <div><span>可信基线</span><strong>{adminRoleLabels[data.role_assignment.trusted_role]}</strong></div>
              <div><span>当前来源</span><strong>{roleSourceLabel(data.role_assignment.source)}</strong></div>
              <div><span>覆盖到期</span><strong>{data.role_assignment.override_expires_at ? formatTime(data.role_assignment.override_expires_at) : '无自动到期'}</strong></div>
            </div>
            {data.role_assignment.source === 'manual' && (
              <div className="notice warning">
                <ShieldAlert size={16} />
                <div><strong>可信角色仍为{adminRoleLabels[data.role_assignment.trusted_role]}</strong><span>OIDC 登录只更新可信基线；当前手工覆盖会保持到显式撤销或到期。</span></div>
              </div>
            )}
            {canManageRole && (
              <form className="user-governance-form" onSubmit={(event) => {
                event.preventDefault()
                setRoleOverride.mutate()
              }}>
                <label>
                  <span>覆盖角色</span>
                  <select aria-label="用户覆盖角色" value={overrideRole} onChange={(event) => setOverrideRole(event.target.value as AdminRole)}>
                    <option value="admin">管理员</option>
                    <option value="operator">运营者</option>
                    <option value="viewer">只读访客</option>
                  </select>
                </label>
                <label>
                  <span>覆盖到期时间</span>
                  <input
                    aria-label="用户角色覆盖到期时间"
                    type="datetime-local"
                    value={overrideExpiresAt}
                    onChange={(event) => setOverrideExpiresAt(event.target.value)}
                  />
                </label>
                {isCurrentUser && overrideRole !== 'admin' && (
                  <div className="notice warning governance-warning">
                    <ShieldAlert size={16} />
                    <div><strong>自我降权会立即生效</strong><span>保存后当前会话将失去角色治理权限；请确认有另一位管理员或设置自动到期时间。</span></div>
                  </div>
                )}
                <div className="governance-confirmation">
                  <label htmlFor={`role-confirmation-${userId}`}>逐字输入 <code>{roleConfirmationText}</code></label>
                  <input
                    id={`role-confirmation-${userId}`}
                    aria-label="用户角色覆盖确认短语"
                    value={roleConfirmation}
                    onChange={(event) => setRoleConfirmation(event.target.value)}
                  />
                  <button className="primary-button" disabled={roleConfirmation !== roleConfirmationText || setRoleOverride.isPending}>保存角色覆盖</button>
                </div>
              </form>
            )}
            {canManageRole && data.role_assignment.source === 'manual' && (
              <div className="governance-confirmation danger-confirmation">
                <label htmlFor={`role-revoke-confirmation-${userId}`}>撤销覆盖并恢复可信基线，逐字输入 <code>{revokeRoleConfirmationText}</code></label>
                <input
                  id={`role-revoke-confirmation-${userId}`}
                  aria-label="撤销用户角色覆盖确认短语"
                  value={revokeRoleConfirmation}
                  onChange={(event) => setRevokeRoleConfirmation(event.target.value)}
                />
                <button className="danger-button" type="button" disabled={revokeRoleConfirmation !== revokeRoleConfirmationText || revokeRoleOverride.isPending} onClick={() => revokeRoleOverride.mutate()}>撤销角色覆盖</button>
              </div>
            )}
          </>
        )}
        {(setRoleOverride.error || revokeRoleOverride.error) && <div className="notice error" role="alert">{(setRoleOverride.error ?? revokeRoleOverride.error)?.message}</div>}
      </div>

      <div className="identity-section">
        <h3><Link2 size={14} /> 外部身份绑定</h3>
        {data.external_identities.length === 0 ? (
          <p className="empty-inline">当前为本地开发身份，没有 OIDC 外部身份绑定。</p>
        ) : data.external_identities.map((identity) => (
          <div className="identity-row" key={identity.id}>
            <div><span>签发方</span><strong>{identity.issuer}</strong></div>
            <div><span>主体</span><code>{identity.subject}</code></div>
            <div><span>最近认证</span><strong>{formatTime(identity.last_authenticated_at)}</strong></div>
          </div>
        ))}
      </div>

      <div className="identity-section">
        <h3><Clock3 size={14} /> 管理会话</h3>
        {isCurrentUser && data.admin_sessions.some((item) => item.revoked_at === null) && (
          <div className="notice warning">
            如果撤销的会话正被本页面使用，操作完成后可能立即需要重新登录。
          </div>
        )}
        {data.admin_sessions.length === 0 ? (
          <p className="empty-inline">本地开发身份不创建或伪造 OIDC 管理会话。</p>
        ) : data.admin_sessions.map((session) => {
          const expired = new Date(session.expires_at).getTime() <= now
          const active = session.revoked_at === null && !expired
          const expected = `确认撤销管理会话 ${session.id}`
          return (
            <div className="identity-row session-row" key={session.id}>
              <div><span>会话 ID</span><code>{session.id}</code></div>
              <div><span>最近活动</span><strong>{formatTime(session.last_seen_at)}</strong></div>
              <div>
                <span>状态</span>
                <strong>{session.revoked_at ? `已撤销 · ${formatTime(session.revoked_at)}` : expired ? '已过期' : `有效至 ${formatTime(session.expires_at)}`}</strong>
              </div>
              {active && canWrite && (
                <button className="danger-button" type="button" onClick={() => {
                  setTargetSessionId(session.id)
                  setConfirmation('')
                }}><LogOut size={14} /> 撤销</button>
              )}
              {targetSessionId === session.id && (
                <div className="session-revoke-confirmation">
                  <label htmlFor={`revoke-${session.id}`}>逐字输入 <code>{expected}</code></label>
                  <input
                    id={`revoke-${session.id}`}
                    aria-label="管理会话撤销确认短语"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                  />
                  <button
                    className="danger-button"
                    type="button"
                    disabled={confirmation !== selectedConfirmation || revoke.isPending}
                    onClick={() => revoke.mutate({ sessionId: session.id })}
                  >确认撤销</button>
                  <button className="secondary-button" type="button" onClick={() => setTargetSessionId(null)}>取消</button>
                </div>
              )}
            </div>
          )
        })}
        {revoke.error && <div className="notice error" role="alert">{revoke.error.message}</div>}
      </div>

      <div className="identity-section">
        <h3><MessagesSquare size={14} /> 会话成员关系</h3>
        {data.conversation_memberships.length === 0 ? (
          <p className="empty-inline">当前用户尚未参与会话。</p>
        ) : data.conversation_memberships.map((membership) => (
          <div className="identity-row" key={membership.conversation_id}>
            <div><span>会话</span><strong>{membership.title}</strong><code>{membership.conversation_id}</code></div>
            <div><span>成员角色</span><strong>{membership.role}</strong></div>
            <div><span>状态</span><strong>{membership.deleted_at ? '已删除' : membership.status === 'active' ? '进行中' : '已归档'}</strong></div>
            <a className="secondary-button" href="/conversations">前往会话管理</a>
          </div>
        ))}
      </div>
    </section>
  )
}
