import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock3, KeyRound, Link2, LogOut, MessagesSquare, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import { getManagedUserDetail, revokeManagedUserSession } from '../api'
import { adminRoleLabels } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN')

export function UserIdentityPanel({
  userId,
  currentUserId,
  canWrite,
}: {
  userId: string
  currentUserId?: string
  canWrite: boolean
}) {
  const queryClient = useQueryClient()
  const [confirmation, setConfirmation] = useState('')
  const [targetSessionId, setTargetSessionId] = useState<string | null>(null)
  const detail = useQuery({
    queryKey: ['managed-user-detail', userId],
    queryFn: () => getManagedUserDetail(userId),
  })
  const revoke = useMutation({
    mutationFn: ({ sessionId }: { sessionId: string }) =>
      revokeManagedUserSession(userId, sessionId, confirmation),
    onSuccess: async () => {
      setConfirmation('')
      setTargetSessionId(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['managed-user-detail', userId] }),
        invalidateAcrossTabs(queryClient, ['audit-records']),
      ])
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
  const selectedConfirmation = targetSessionId
    ? `确认撤销管理会话 ${targetSessionId}`
    : ''

  return (
    <section className="panel user-identity-panel" aria-label="用户身份治理">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">身份治理详情</p>
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
          <span>管理角色</span>
          <strong>{data.role_assignment ? adminRoleLabels[data.role_assignment.role] : '未分配'}</strong>
          <small>{data.role_assignment?.source === 'development' ? '本地开发环境' : data.role_assignment ? 'OIDC 可信声明同步' : '没有角色来源'}</small>
        </article>
        <article>
          <Link2 size={16} />
          <span>OIDC 绑定</span>
          <strong>{data.external_identities.length} 个</strong>
          <small>{data.external_identities.length === 0 ? '没有伪造外部身份' : '仅展示已验证主体'}</small>
        </article>
        <article>
          <MessagesSquare size={16} />
          <span>参与会话</span>
          <strong>{data.conversation_memberships.length} 个</strong>
          <small>不展示消息正文</small>
        </article>
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
        {currentUserId === userId && data.admin_sessions.some((item) => item.revoked_at === null) && (
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
