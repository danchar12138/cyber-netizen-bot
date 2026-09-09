import { useQuery } from '@tanstack/react-query'
import { ScrollText } from 'lucide-react'
import { useCallback, useMemo } from 'react'

import { type AuditRecord, formatConfigValue, getAuditRecords } from '../api'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'

export function AuditLogPage() {
  const audit = useQuery({ queryKey: ['audit-records'], queryFn: () => getAuditRecords() })
  const columns = useMemo<Array<AdminTableColumn<AuditRecord>>>(() => [
    {
      key: 'action',
      label: '操作',
      render: (row) => <div className="table-primary"><strong>{row.action}</strong><code>#{row.id}</code></div>,
    },
    { key: 'resource', label: '资源', render: (row) => <span>{row.resource_type}<code className="block-code">{row.resource_id ?? '批量/系统'}</code></span> },
    { key: 'actor', label: '操作者 ID', render: (row) => <code>{row.actor_id ?? '系统'}</code> },
    {
      key: 'detail',
      label: '安全详情',
      render: (row) => <code className="audit-detail">{formatConfigValue(row.detail)}</code>,
    },
    { key: 'time', label: '发生时间', render: (row) => new Date(row.created_at).toLocaleString('zh-CN') },
  ], [])
  const searchableText = useCallback(
    (row: AuditRecord) => `${row.action} ${row.resource_type} ${row.resource_id ?? ''} ${row.actor_id ?? ''}`,
    [],
  )

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">变更可追溯</p>
          <h1>审计日志</h1>
          <p>查询配置、密钥与资源管理操作；详情只保存安全元数据，不保存凭证明文。</p>
        </div>
      </section>
      <div className="notice info">
        <ScrollText size={17} />
        <div><strong>只追加审计</strong><span>当前展示当前租户和允许查看的系统级操作，按最新时间排序。</span></div>
      </div>
      <section className="panel table-panel">
        <AdminDataTable
          rows={audit.data?.items ?? []}
          columns={columns}
          rowKey={(row) => String(row.id)}
          searchableText={searchableText}
          searchPlaceholder="搜索操作、资源或操作者"
          emptyMessage={audit.isLoading ? '正在读取审计日志…' : '暂无审计记录'}
        />
      </section>
    </div>
  )
}
