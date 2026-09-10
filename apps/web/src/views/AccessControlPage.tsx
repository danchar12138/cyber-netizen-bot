import { useQuery } from '@tanstack/react-query'
import { LockKeyhole, ShieldCheck } from 'lucide-react'
import { useCallback } from 'react'

import { type AdminRoleDefinition, getAdminRoles, getAdminSession } from '../api'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import { adminRoleLabels, authenticationModeLabels, displayLabel } from '../displayLabels'

const permissionLabels: Record<string, string> = {
  'dashboard:read': '查看总览',
  'configuration:read': '查看配置',
  'configuration:write': '发布配置',
  'secret:manage': '管理密钥',
  'conversation:read': '查看对话',
  'conversation:use': '操作对话',
  'access_control:read': '查看权限',
  'agent:read': '查看 Agent',
  'agent:write': '管理 Agent 状态',
  'user:read': '查看用户',
  'user:write': '管理用户状态',
  'audit:read': '查看审计',
}

const columns: Array<AdminTableColumn<AdminRoleDefinition>> = [
  {
    key: 'role',
    label: '角色',
    render: (row) => <div className="table-primary"><strong>{row.label}</strong></div>,
  },
  { key: 'description', label: '职责边界', render: (row) => row.description },
  {
    key: 'permissions',
    label: '已授权能力',
    render: (row) => <div className="permission-tags">{row.permissions.map((item) => <span key={item}>{permissionLabels[item] ?? item}</span>)}</div>,
  },
]

export function AccessControlPage() {
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const roles = useQuery({ queryKey: ['admin-roles'], queryFn: getAdminRoles })
  const searchableText = useCallback(
    (row: AdminRoleDefinition) => `${row.role} ${row.label} ${row.description} ${row.permissions.join(' ')}`,
    [],
  )

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">最小权限控制</p>
          <h1>访问控制</h1>
          <p>服务端按能力校验每个管理请求；界面权限只用于解释和减少误操作。</p>
        </div>
      </section>

      <div className="notice info">
        <ShieldCheck size={17} />
        <div>
          <strong>当前会话：{session.data?.display_name ?? '读取中'} · {session.data ? adminRoleLabels[session.data.role] : '—'}</strong>
          <span>认证模式：{session.data ? displayLabel(authenticationModeLabels, session.data.authentication_mode) : '—'}；OIDC 角色由可信签名 claim 映射并由服务端执行最小权限校验。</span>
        </div>
      </div>

      <section className="panel access-summary">
        <LockKeyhole size={20} />
        <div>
          <strong>角色能力矩阵</strong>
          <p>管理员负责密钥与全部变更；运营者可发布普通配置和操作对话；只读访客不能产生变更。</p>
        </div>
      </section>

      <section className="panel table-panel">
        {roles.isError && <div className="empty-state error">无法读取角色能力矩阵。</div>}
        <AdminDataTable
          rows={roles.data?.roles ?? []}
          columns={columns}
          rowKey={(row) => row.role}
          searchableText={searchableText}
          searchPlaceholder="搜索角色或权限"
          emptyMessage={roles.isLoading ? '正在读取角色…' : '没有匹配的角色'}
        />
      </section>
    </div>
  )
}
