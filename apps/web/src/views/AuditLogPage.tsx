import { useQuery } from '@tanstack/react-query'
import { Search, ScrollText, X } from 'lucide-react'
import { useCallback, useMemo, useState, type FormEvent } from 'react'

import { type AuditRecord, getAuditRecords } from '../api'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import {
  auditActionLabels,
  auditResourceLabels,
  displayLabel,
  formatMetadataEntries,
} from '../displayLabels'

export function AuditLogPage() {
  const [actionInput, setActionInput] = useState('')
  const [resourceTypeInput, setResourceTypeInput] = useState('')
  const [resourceIdInput, setResourceIdInput] = useState('')
  const [filters, setFilters] = useState({ action: '', resourceType: '', resourceId: '' })
  const audit = useQuery({
    queryKey: ['audit-records', filters],
    queryFn: () => getAuditRecords('', filters.action, filters.resourceType, filters.resourceId),
  })
  const submitFilters = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setFilters({
      action: actionInput.trim(),
      resourceType: resourceTypeInput.trim(),
      resourceId: resourceIdInput.trim(),
    })
  }, [actionInput, resourceIdInput, resourceTypeInput])
  const clearFilters = useCallback(() => {
    setActionInput('')
    setResourceTypeInput('')
    setResourceIdInput('')
    setFilters({ action: '', resourceType: '', resourceId: '' })
  }, [])
  const columns = useMemo<Array<AdminTableColumn<AuditRecord>>>(() => [
    {
      key: 'action',
      label: '操作',
      render: (row) => <div className="table-primary"><strong>{displayLabel(auditActionLabels, row.action)}</strong><code>#{row.id}</code></div>,
    },
    { key: 'resource', label: '资源', render: (row) => <span>{displayLabel(auditResourceLabels, row.resource_type)}<code className="block-code">{row.resource_id ?? '批量/系统'}</code></span> },
    { key: 'actor', label: '操作者 ID', render: (row) => <code>{row.actor_id ?? '系统'}</code> },
    {
      key: 'detail',
      label: '安全详情',
      render: (row) => <span className="audit-detail">{formatMetadataEntries(row.detail)}</span>,
    },
    { key: 'time', label: '发生时间', render: (row) => new Date(row.created_at).toLocaleString('zh-CN') },
  ], [])
  const searchableText = useCallback(
    (row: AuditRecord) => `${row.action} ${displayLabel(auditActionLabels, row.action)} ${row.resource_type} ${displayLabel(auditResourceLabels, row.resource_type)} ${row.resource_id ?? ''} ${row.actor_id ?? ''}`,
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
        <form className="admin-table-toolbar audit-filter-toolbar" onSubmit={submitFilters}>
          <label className="status-filter"><span>操作</span><input value={actionInput} onChange={(event) => setActionInput(event.target.value)} placeholder="如 configuration.published" maxLength={120} /></label>
          <label className="status-filter"><span>资源类型</span><input value={resourceTypeInput} onChange={(event) => setResourceTypeInput(event.target.value)} placeholder="如 configuration_version" maxLength={120} /></label>
          <label className="status-filter"><span>资源 ID</span><input value={resourceIdInput} onChange={(event) => setResourceIdInput(event.target.value)} placeholder="精确匹配资源 ID" maxLength={255} /></label>
          <div className="table-actions"><button className="secondary-button" type="submit"><Search size={14} />查询</button><button className="icon-button" type="button" title="清除审计筛选" aria-label="清除审计筛选" onClick={clearFilters}><X size={14} /></button></div>
        </form>
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
