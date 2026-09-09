import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Eye, FileText, MessageSquareText, Pencil, Pin, RotateCcw, Trash2, X } from 'lucide-react'
import { useCallback, useState } from 'react'

import {
  type Conversation,
  deleteConversation,
  getAdminSession,
  getAttachments,
  getConversations,
  getMessages,
  updateConversation,
} from '../api'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import { invalidateAcrossTabs } from '../tabSync'

const messageStatusLabels = {
  received: '已接收',
  processing: '处理中',
  streaming: '正在输入',
  completed: '已完成',
  suppressed: '未回复',
  cancelled: '已取消',
  failed: '失败',
} as const

export function ConversationsPage() {
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'archived'>('all')
  const [detailId, setDetailId] = useState<string | null>(null)
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canWrite = session.data?.permissions.includes('conversation:use') ?? false
  const conversations = useQuery({
    queryKey: ['conversations', 'management', statusFilter],
    queryFn: () => getConversations('', statusFilter === 'all' ? undefined : statusFilter),
  })
  const messages = useQuery({
    queryKey: ['messages', detailId],
    queryFn: () => getMessages(detailId!),
    enabled: detailId !== null,
  })
  const attachments = useQuery({
    queryKey: ['attachments', detailId],
    queryFn: () => getAttachments(detailId!),
    enabled: detailId !== null,
  })
  const refresh = () => invalidateAcrossTabs(queryClient, ['conversations'])
  const updateMutation = useMutation({
    mutationFn: ({ id, command }: {
      id: string
      command: { title?: string; status?: 'active' | 'archived'; pinned?: boolean }
    }) => updateConversation(id, command),
    onSuccess: refresh,
  })
  const deleteMutation = useMutation({ mutationFn: deleteConversation, onSuccess: refresh })

  const rename = (row: Conversation) => {
    const title = window.prompt('请输入新的会话标题', row.title)?.trim()
    if (title && title !== row.title) updateMutation.mutate({ id: row.id, command: { title } })
  }
  const columns: Array<AdminTableColumn<Conversation>> = [
    {
      key: 'title',
      label: '会话',
      render: (row) => (
        <div className="table-primary">
          <strong>{row.title}</strong><code>{row.id}</code>
        </div>
      ),
    },
    {
      key: 'status',
      label: '状态',
      render: (row) => (
        <span className={`entity-status ${row.status === 'active' ? 'active' : 'disabled'}`}>
          {row.status === 'active' ? '进行中' : '已归档'}{row.pinned_at ? ' · 置顶' : ''}
        </span>
      ),
    },
    {
      key: 'branch',
      label: '来源',
      render: (row) => row.branched_from_conversation_id
        ? <span>消息编辑分支<code className="block-code">{row.branched_from_conversation_id}</code></span>
        : '原始会话',
    },
    {
      key: 'updated',
      label: '最后更新',
      render: (row) => new Date(row.updated_at).toLocaleString('zh-CN'),
    },
    {
      key: 'actions',
      label: '操作',
      render: (row) => (
        <div className="table-actions">
          <button onClick={() => setDetailId(row.id)}><Eye size={13} />查看消息</button>
          <button disabled={!canWrite} aria-label={`重命名 ${row.title}`} onClick={() => rename(row)}><Pencil size={13} />重命名</button>
          <button disabled={!canWrite} onClick={() => updateMutation.mutate({ id: row.id, command: { pinned: !row.pinned_at } })}><Pin size={13} />{row.pinned_at ? '取消置顶' : '置顶'}</button>
          <button disabled={!canWrite} onClick={() => updateMutation.mutate({ id: row.id, command: { status: row.status === 'active' ? 'archived' : 'active' } })}>
            {row.status === 'active' ? <Archive size={13} /> : <RotateCcw size={13} />}{row.status === 'active' ? '归档' : '恢复'}
          </button>
          <button className="danger" disabled={!canWrite} onClick={() => {
            if (window.confirm(`确认软删除“${row.title}”？`)) deleteMutation.mutate(row.id)
          }}><Trash2 size={13} />删除</button>
        </div>
      ),
    },
  ]
  const searchableText = useCallback(
    (row: Conversation) => `${row.title} ${row.id} ${row.status}`,
    [],
  )
  const operationError = updateMutation.error ?? deleteMutation.error
  const detailConversation = conversations.data?.items.find((item) => item.id === detailId)

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">会话治理</p>
          <h1>会话与消息</h1>
          <p>统一查询、置顶、归档和软删除会话；消息全文检索与交互位于内部对话工作台。</p>
        </div>
        <label className="status-filter">
          状态
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}>
            <option value="all">全部</option><option value="active">进行中</option><option value="archived">已归档</option>
          </select>
        </label>
      </section>
      <div className="notice info">
        <MessageSquareText size={17} />
        <div><strong>{conversations.data?.items.length ?? 0} 个会话</strong><span>删除采用软删除策略，后续由数据保留任务执行最终清理。</span></div>
      </div>
      {operationError && <div className="notice error">{operationError.message}</div>}
      <section className="panel table-panel">
        <AdminDataTable
          rows={conversations.data?.items ?? []}
          columns={columns}
          rowKey={(row) => row.id}
          searchableText={searchableText}
          searchPlaceholder="搜索会话标题或 ID"
          emptyMessage={conversations.isLoading ? '正在读取会话…' : '暂无匹配会话'}
        />
      </section>
      {detailId && (
        <section className="panel conversation-detail" aria-labelledby="conversation-detail-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">消息详情</p>
              <h2 id="conversation-detail-heading">{detailConversation?.title ?? '会话详情'}</h2>
            </div>
            <button className="icon-button" aria-label="关闭消息详情" onClick={() => setDetailId(null)}><X size={16} /></button>
          </div>
          {messages.isLoading && <p className="empty-copy">正在读取消息…</p>}
          {!messages.isLoading && !messages.data?.items.length && <p className="empty-copy">此会话暂无消息。</p>}
          <div className="conversation-detail-list">
            {messages.data?.items.map((message) => {
              const messageAttachments = attachments.data?.items.filter(
                (attachment) => attachment.message_id === message.id,
              ) ?? []
              return (
                <article key={message.id}>
                  <div><strong>{message.sender_type === 'agent' ? 'Agent' : message.sender_type === 'user' ? '用户' : '系统'}</strong><span>{new Date(message.created_at).toLocaleString('zh-CN')} · {messageStatusLabels[message.status]}</span></div>
                  <p>{message.content}</p>
                  {messageAttachments.map((attachment) => <small key={attachment.id}><FileText size={12} />{attachment.original_name}</small>)}
                </article>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
