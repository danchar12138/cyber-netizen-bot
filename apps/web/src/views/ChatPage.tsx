import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive, Bot, CircleStop, Copy, CornerDownLeft, ImagePlus, LoaderCircle,
  FileText, MemoryStick, Menu, Paperclip, Pencil, Pin, RotateCcw, Search,
  SendHorizontal, Sparkles, ThumbsDown, ThumbsUp, Trash2, UserRound, UsersRound, X,
} from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'

import {
  type ChatAttachment, type ChatMessage, type ConversationEvent, type MessageAccepted,
  cancelAgentRun, clearMessageFeedback, completeAttachment, createConversation,
  deleteAttachment, deleteConversation, editChatMessage, getAdminSession,
  getApiAccessToken, getAttachmentPreview, getAttachments, getConversations, getDevelopmentIdentity,
  getMessageFeedback, getMessages, getRelationship, recallMemories,
  regenerateChatMessage, reserveAttachment,
  searchChatMessages, sendChatMessage, setMessageFeedback, updateConversation,
  uploadReservedAttachment,
} from '../api'
import { applyConversationEvent } from '../chatEvents'
import { memoryKindLabels, relationshipStageLabels } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

const MarkdownContent = lazy(() => import('../components/MarkdownContent'))

const messageStatusLabels: Record<ChatMessage['status'], string> = {
  received: '已接收', processing: '处理中', streaming: '正在输入',
  completed: '已完成', suppressed: '未回复', cancelled: '已取消', failed: '失败',
}

interface DraftAttachment {
  localId: string
  name: string
  progress: number
  status: 'preparing' | 'uploading' | 'ready' | 'failed'
  attachmentId?: string
  error?: string
}

function draftStorageKey(conversationId: string) {
  return `cnb-chat-draft:${conversationId}`
}

function readDraft(conversationId: string): string {
  try {
    return window.localStorage.getItem(draftStorageKey(conversationId)) ?? ''
  } catch {
    return ''
  }
}

function writeDraft(conversationId: string, value: string): void {
  try {
    if (value) window.localStorage.setItem(draftStorageKey(conversationId), value)
    else window.localStorage.removeItem(draftStorageKey(conversationId))
  } catch {
    // 浏览器禁用本地存储时仍允许正常对话，仅不保留草稿。
  }
}

export function ChatPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [draftMessageId, setDraftMessageId] = useState(() => crypto.randomUUID())
  const [draftAttachments, setDraftAttachments] = useState<DraftAttachment[]>([])
  const [searchText, setSearchText] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [showConversationList, setShowConversationList] = useState(false)
  const [connection, setConnection] = useState<'连接中' | '已连接' | '正在重连'>('连接中')
  const lastSequence = useRef(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const imageInput = useRef<HTMLInputElement>(null)

  const identity = useQuery({ queryKey: ['chat-identity'], queryFn: getDevelopmentIdentity })
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canUseConversation = session.data?.permissions.includes('conversation:use') ?? false
  const canReadMemory = session.data?.permissions.includes('memory:read') ?? false
  const conversations = useQuery({
    queryKey: ['conversations', 'chat', searchText],
    queryFn: () => getConversations(searchText),
  })
  const messageSearch = useQuery({
    queryKey: ['message-search', searchText],
    queryFn: () => searchChatMessages(searchText),
    enabled: searchText.trim().length >= 2,
  })
  const messageHistory = useQuery({
    queryKey: ['messages', selectedId],
    queryFn: () => getMessages(selectedId!),
    enabled: selectedId !== null,
  })
  const feedback = useQuery({
    queryKey: ['message-feedback', selectedId],
    queryFn: () => getMessageFeedback(selectedId!),
    enabled: selectedId !== null,
  })
  const attachments = useQuery({
    queryKey: ['attachments', selectedId],
    queryFn: () => getAttachments(selectedId!),
    enabled: selectedId !== null,
  })
  const lastUserText = useMemo(
    () => [...messages].reverse().find((item) => item.sender_type === 'user')?.content ?? '',
    [messages],
  )
  const relationship = useQuery({
    queryKey: ['relationship', identity.data?.user_id],
    queryFn: () => getRelationship(identity.data!.user_id),
    enabled: Boolean(identity.data?.user_id && canReadMemory),
  })
  const recalledMemories = useQuery({
    queryKey: ['chat-memory-recall', identity.data?.user_id, lastUserText],
    queryFn: () => recallMemories(identity.data!.user_id, lastUserText, 5),
    enabled: Boolean(identity.data?.user_id && lastUserText.trim() && canReadMemory),
  })

  useEffect(() => {
    if (!selectedId && conversations.data?.items[0]) {
      setSelectedId(conversations.data.items[0].id)
    }
  }, [conversations.data, selectedId])

  useEffect(() => setMessages(messageHistory.data?.items ?? []), [messageHistory.data])

  useEffect(() => {
    setDraft(selectedId ? readDraft(selectedId) : '')
    setDraftAttachments([])
    setDraftMessageId(crypto.randomUUID())
  }, [selectedId])

  useEffect(() => {
    if (!selectedId) return
    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const accessToken = getApiAccessToken()
      socket = new WebSocket(
        `${protocol}//${window.location.host}/api/v1/chat/conversations/${selectedId}/events?after=${lastSequence.current}`,
        accessToken ? ['cnb.bearer', accessToken] : undefined,
      )
      setConnection(lastSequence.current === 0 ? '连接中' : '正在重连')
      socket.onopen = () => setConnection('已连接')
      socket.onmessage = (frame) => {
        const event = JSON.parse(frame.data as string) as
          | ConversationEvent
          | { event_type: 'system.heartbeat'; last_sequence: number }
        if (!('sequence' in event) || event.sequence <= lastSequence.current) return
        lastSequence.current = event.sequence
        setMessages((current) => applyConversationEvent(current, event))
        if ((event.event_type === 'run.queued' || event.event_type === 'run.started') && event.run_id) {
          setActiveRunId(event.run_id)
        }
        if (['run.completed', 'run.cancelled', 'run.failed'].includes(event.event_type)) {
          setActiveRunId(null)
        }
        if (event.event_type.startsWith('message.feedback.')) {
          void queryClient.invalidateQueries({ queryKey: ['message-feedback', selectedId] })
        }
      }
      socket.onclose = () => {
        if (disposed) return
        setConnection('正在重连')
        reconnectTimer = window.setTimeout(connect, 1000)
      }
    }
    lastSequence.current = 0
    connect()
    return () => {
      disposed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [queryClient, selectedId])

  const acceptRun = (accepted: MessageAccepted) => {
    setMessages((current) => {
      const event = (message: ChatMessage, sequence: number): ConversationEvent => ({
        schema_version: '1', event_id: crypto.randomUUID(),
        conversation_id: message.conversation_id, sequence, event_type: 'message.created',
        occurred_at: message.created_at, run_id: accepted.run.id, message_id: message.id,
        payload: { ...message },
      })
      return applyConversationEvent(
        applyConversationEvent(current, event(accepted.user_message, Number.MAX_SAFE_INTEGER - 1)),
        event(accepted.response_message, Number.MAX_SAFE_INTEGER),
      )
    })
    setActiveRunId(accepted.run.id)
    void invalidateAcrossTabs(queryClient, ['conversations'])
  }

  const createConversationMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: async (conversation) => {
      setSearchText('')
      await invalidateAcrossTabs(queryClient, ['conversations'])
      setSelectedId(conversation.id)
      setShowConversationList(false)
    },
  })
  const updateConversationMutation = useMutation({
    mutationFn: ({ conversationId, command }: {
      conversationId: string
      command: { title?: string; status?: 'active' | 'archived'; pinned?: boolean }
    }) => updateConversation(conversationId, command),
    onSuccess: () => invalidateAcrossTabs(queryClient, ['conversations']),
  })
  const deleteConversationMutation = useMutation({
    mutationFn: deleteConversation,
    onSuccess: async (_, conversationId) => {
      if (selectedId === conversationId) {
        setSelectedId(null)
        setShowConversationList(false)
      }
      await invalidateAcrossTabs(queryClient, ['conversations'])
    },
  })
  const sendMessage = useMutation({
    mutationFn: ({ content, messageId, attachmentIds }: {
      content: string
      messageId: string
      attachmentIds: string[]
    }) => sendChatMessage(selectedId!, {
      client_message_id: messageId, content, attachment_ids: attachmentIds,
    }),
    onSuccess: (accepted) => {
      acceptRun(accepted)
      if (selectedId) writeDraft(selectedId, '')
      setDraft('')
      setDraftAttachments([])
      setDraftMessageId(crypto.randomUUID())
      void invalidateAcrossTabs(queryClient, ['attachments', selectedId])
    },
  })
  const regenerateMessage = useMutation({ mutationFn: regenerateChatMessage, onSuccess: acceptRun })
  const editMessage = useMutation({
    mutationFn: ({ messageId, content }: { messageId: string; content: string }) =>
      editChatMessage(messageId, content),
    onSuccess: async (accepted) => {
      setSearchText('')
      await invalidateAcrossTabs(queryClient, ['conversations'])
      setSelectedId(accepted.run.conversation_id)
      setMessages([accepted.user_message, accepted.response_message])
      setActiveRunId(accepted.run.id)
    },
  })
  const feedbackMutation = useMutation({
    mutationFn: ({ messageId, rating }: { messageId: string; rating: 'positive' | 'negative' }) =>
      setMessageFeedback(messageId, rating),
    onSuccess: () => invalidateAcrossTabs(queryClient, ['message-feedback', selectedId]),
  })
  const clearFeedbackMutation = useMutation({
    mutationFn: clearMessageFeedback,
    onSuccess: () => invalidateAcrossTabs(queryClient, ['message-feedback', selectedId]),
  })
  const cancelRun = useMutation({
    mutationFn: (runId: string) => cancelAgentRun(runId),
    onSuccess: () => setActiveRunId(null),
  })

  const selectedConversation = useMemo(
    () => conversations.data?.items.find((item) => item.id === selectedId),
    [conversations.data, selectedId],
  )
  const feedbackByMessage = useMemo(
    () => new Map(feedback.data?.items.map((item) => [item.message_id, item.rating])),
    [feedback.data],
  )
  const operationError = createConversationMutation.error ?? updateConversationMutation.error ??
    deleteConversationMutation.error ?? sendMessage.error ?? regenerateMessage.error ??
    editMessage.error ?? feedbackMutation.error ?? clearFeedbackMutation.error ?? cancelRun.error
  const draftAttachmentsBlocked = draftAttachments.some((item) => item.status !== 'ready')

  const updateDraft = (value: string) => {
    setDraft(value)
    if (selectedId) writeDraft(selectedId, value)
  }

  const submit = () => {
    const content = draft.trim()
    const ready = draftAttachments.filter((item) => item.status === 'ready')
    if (content && selectedId && !activeRunId && !sendMessage.isPending && !draftAttachmentsBlocked) {
      sendMessage.mutate({
        content,
        messageId: draftMessageId,
        attachmentIds: ready.flatMap((item) => item.attachmentId ? [item.attachmentId] : []),
      })
    }
  }
  const renameConversation = (conversationId: string, currentTitle: string) => {
    const title = window.prompt('请输入新的会话标题', currentTitle)?.trim()
    if (title && title !== currentTitle) updateConversationMutation.mutate({ conversationId, command: { title } })
  }
  const editUserMessage = (message: ChatMessage) => {
    const content = window.prompt('编辑消息后将创建新会话分支', message.content)?.trim()
    if (content && content !== message.content) editMessage.mutate({ messageId: message.id, content })
  }
  const toggleFeedback = (messageId: string, rating: 'positive' | 'negative') => {
    if (feedbackByMessage.get(messageId) === rating) clearFeedbackMutation.mutate(messageId)
    else feedbackMutation.mutate({ messageId, rating })
  }
  const updateDraftAttachment = (localId: string, patch: Partial<DraftAttachment>) => {
    setDraftAttachments((current) => current.map((item) =>
      item.localId === localId ? { ...item, ...patch } : item,
    ))
  }
  const uploadFiles = async (files: FileList | null) => {
    if (!files || !selectedId) return
    for (const file of [...files].slice(0, Math.max(0, 10 - draftAttachments.length))) {
      const localId = crypto.randomUUID()
      setDraftAttachments((current) => [...current, {
        localId, name: file.name, progress: 0, status: 'preparing',
      }])
      try {
        const reservation = await reserveAttachment(selectedId, draftMessageId, file)
        updateDraftAttachment(localId, {
          attachmentId: reservation.attachment.id, status: 'uploading', progress: 1,
        })
        await uploadReservedAttachment(
          reservation.upload,
          file,
          (progress) => updateDraftAttachment(localId, { progress }),
        )
        await completeAttachment(reservation.attachment.id)
        updateDraftAttachment(localId, { status: 'ready', progress: 100 })
        await invalidateAcrossTabs(queryClient, ['attachments', selectedId])
      } catch (error) {
        updateDraftAttachment(localId, {
          status: 'failed',
          error: error instanceof Error ? error.message : '附件上传失败',
        })
      }
    }
    if (fileInput.current) fileInput.current.value = ''
    if (imageInput.current) imageInput.current.value = ''
  }
  const removeDraftAttachment = async (item: DraftAttachment) => {
    if (item.attachmentId) await deleteAttachment(item.attachmentId).catch(() => undefined)
    setDraftAttachments((current) => current.filter((candidate) => candidate.localId !== item.localId))
  }
  const previewAttachment = async (attachment: ChatAttachment) => {
    const preview = await getAttachmentPreview(attachment.id)
    window.open(preview.url, '_blank', 'noopener,noreferrer')
  }
  const selectConversation = (conversationId: string) => {
    setSelectedId(conversationId)
    setShowConversationList(false)
  }

  return (
    <div className="page chat-page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">内部对话工作台</p>
          <h1 id="chat-heading">内部对话</h1>
          <p>管理会话、搜索消息、创建分支与反馈，并按事件序号恢复流式响应。</p>
        </div>
        <span className={`phase-tag connection ${connection === '已连接' ? 'online' : ''}`} role="status" aria-live="polite">{connection}</span>
      </section>
      {operationError && <div className="notice error">{operationError.message}</div>}
      <section className="chat-workspace panel" aria-labelledby="chat-heading">
        <aside className={`conversation-list ${showConversationList ? 'mobile-open' : ''}`} aria-label="会话列表">
          <button className="new-conversation" onClick={() => createConversationMutation.mutate()}
            disabled={!canUseConversation || createConversationMutation.isPending}>
            <Sparkles size={15} /> 新建会话
          </button>
          <label className="chat-search">
            <Search size={14} />
            <input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="搜索会话或消息" />
          </label>
          {messageSearch.data?.items.length ? (
            <div className="message-search-results" aria-label="消息搜索结果">
              <p className="nav-label">消息命中</p>
              {messageSearch.data.items.slice(0, 5).map((result) => (
                <button key={result.message.id} onClick={() => selectConversation(result.conversation.id)}>
                  <strong>{result.conversation.title}</strong><span>{result.message.content}</span>
                </button>
              ))}
            </div>
          ) : null}
          <p className="nav-label">会话</p>
          {conversations.data?.items.map((conversation) => (
            <div className={`conversation-row ${conversation.id === selectedId ? 'active' : ''}`} key={conversation.id}>
              <button className="conversation" onClick={() => selectConversation(conversation.id)}>
                <strong>{conversation.title}</strong>
                <span>{conversation.status === 'active' ? '进行中' : '已归档'}{conversation.pinned_at ? ' · 已置顶' : ''}</span>
              </button>
              {canUseConversation && (
                <div className="conversation-actions">
                  <button aria-label={`重命名 ${conversation.title}`} onClick={() => renameConversation(conversation.id, conversation.title)}><Pencil size={12} /></button>
                  <button aria-label={`${conversation.pinned_at ? '取消置顶' : '置顶'} ${conversation.title}`} onClick={() => updateConversationMutation.mutate({ conversationId: conversation.id, command: { pinned: !conversation.pinned_at } })}><Pin size={12} /></button>
                  <button aria-label={`${conversation.status === 'active' ? '归档' : '恢复'} ${conversation.title}`} onClick={() => updateConversationMutation.mutate({ conversationId: conversation.id, command: { status: conversation.status === 'active' ? 'archived' : 'active' } })}><Archive size={12} /></button>
                  <button aria-label={`删除 ${conversation.title}`} onClick={() => {
                    if (window.confirm(`确定删除“${conversation.title}”吗？数据将在保留期后清理。`)) deleteConversationMutation.mutate(conversation.id)
                  }}><Trash2 size={12} /></button>
                </div>
              )}
            </div>
          ))}
          {!conversations.isLoading && !conversations.data?.items.length && <p className="empty-copy">没有符合条件的会话。</p>}
        </aside>
        <div className="conversation-main">
          <div className="conversation-header">
            <button
              className="mobile-conversation-toggle"
              type="button"
              aria-label={showConversationList ? '隐藏会话列表' : '显示会话列表'}
              aria-expanded={showConversationList}
              onClick={() => setShowConversationList((current) => !current)}
            >
              <Menu size={18} />
            </button>
            <div className="agent-avatar"><Bot size={20} /></div>
            <div><strong>{identity.data?.agent_name ?? '赛博网友'}</strong><span>{selectedConversation?.title ?? '请选择或创建会话'}</span></div>
          </div>
          <div className={`message-stage ${messages.length ? 'has-messages' : ''}`} aria-live="polite">
            {messageHistory.isLoading && <LoaderCircle className="spin" size={24} />}
            {!messageHistory.isLoading && messages.length === 0 && (
              <div className="chat-empty">
                <div className="welcome-orb"><Bot size={28} /></div>
                <h2>{selectedId ? '从一句真心话开始吧' : '对话工作台已经就位'}</h2>
                <p>{selectedId ? '可以直接聊天，也可以从历史消息创建分支。' : '创建会话后即可开始。'}</p>
              </div>
            )}
            {messages.filter((message) => message.status !== 'suppressed').map((message) => {
              const selectedFeedback = feedbackByMessage.get(message.id)
              const messageAttachments = attachments.data?.items.filter((item) => item.message_id === message.id) ?? []
              return (
                <article className={`chat-message ${message.sender_type}`} key={message.id}>
                  <div className="message-avatar">{message.sender_type === 'agent' ? <Bot size={15} /> : <UserRound size={15} />}</div>
                  <div className="message-bubble">
                    <div className="message-meta">
                      <strong>{message.sender_type === 'agent' ? identity.data?.agent_name ?? 'Agent' : '我'}</strong>
                      <span>{message.edited_from_id ? '分支消息 · ' : ''}{messageStatusLabels[message.status]}</span>
                    </div>
                    <div className="message-content">
                      <Suspense fallback={<p>{message.content || '正在读取内容…'}</p>}>
                        <MarkdownContent content={message.content || (message.status === 'processing' ? '正在思考…' : '…')} />
                      </Suspense>
                    </div>
                    {messageAttachments.length > 0 && (
                      <div className="message-attachments">
                        {messageAttachments.map((attachment) => (
                          <button key={attachment.id} onClick={() => void previewAttachment(attachment)}>
                            {attachment.content_type.startsWith('image/') ? <ImagePlus size={13} /> : <FileText size={13} />} {attachment.original_name}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="message-actions">
                      <button aria-label="复制消息" onClick={() => void navigator.clipboard.writeText(message.content)}><Copy size={13} /></button>
                      <button aria-label="引用消息" onClick={() => updateDraft(`> ${message.content.replaceAll('\n', '\n> ')}\n\n`)}><CornerDownLeft size={13} /></button>
                      {message.sender_type === 'user' && canUseConversation && <button aria-label="编辑并创建分支" onClick={() => editUserMessage(message)}><Pencil size={13} /></button>}
                      {message.sender_type === 'agent' && canUseConversation && (
                        <>
                          <button className={selectedFeedback === 'positive' ? 'selected' : ''} aria-label="有帮助" onClick={() => toggleFeedback(message.id, 'positive')}><ThumbsUp size={13} /></button>
                          <button className={selectedFeedback === 'negative' ? 'selected' : ''} aria-label="没帮助" onClick={() => toggleFeedback(message.id, 'negative')}><ThumbsDown size={13} /></button>
                          <button aria-label="重新生成" disabled={Boolean(activeRunId)} onClick={() => regenerateMessage.mutate(message.id)}><RotateCcw size={13} /></button>
                        </>
                      )}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
          <div className="composer-shell">
            {draftAttachments.length > 0 && (
              <div className="draft-attachments" aria-label="待发送附件">
                {draftAttachments.map((item) => (
                  <div className="draft-attachment" key={item.localId}>
                    <FileText size={14} />
                    <span>{item.name}</span>
                    <small>{item.status === 'ready' ? '已就绪' : item.status === 'failed' ? item.error : `${item.progress}%`}</small>
                    <button aria-label={`移除 ${item.name}`} onClick={() => void removeDraftAttachment(item)}><X size={13} /></button>
                  </div>
                ))}
              </div>
            )}
            <textarea aria-label="消息内容" value={draft} onChange={(event) => updateDraft(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }}
              disabled={!canUseConversation || !selectedId}
              placeholder={selectedId ? '输入消息，Enter 发送，Shift + Enter 换行' : '请先创建会话'} />
            <div className="composer-actions">
              <div>
                <input ref={fileInput} type="file" hidden multiple onChange={(event) => void uploadFiles(event.target.files)} />
                <button aria-label="添加附件" disabled={!canUseConversation || !selectedId} onClick={() => fileInput.current?.click()}><Paperclip size={17} /></button>
                <input ref={imageInput} type="file" accept="image/*" hidden multiple onChange={(event) => void uploadFiles(event.target.files)} />
                <button aria-label="添加图片" disabled={!canUseConversation || !selectedId} onClick={() => imageInput.current?.click()}><ImagePlus size={17} /></button>
              </div>
              {activeRunId ? (
                <button className="stop-button" aria-label="停止生成" onClick={() => cancelRun.mutate(activeRunId)} disabled={!canUseConversation || cancelRun.isPending}><CircleStop size={17} /></button>
              ) : (
                <button className="send-button" aria-label="发送消息" onClick={submit} disabled={!canUseConversation || !selectedId || !draft.trim() || draftAttachmentsBlocked || sendMessage.isPending}><SendHorizontal size={16} /></button>
              )}
            </div>
          </div>
        </div>
        <aside className="chat-context-panel" aria-label="关系与召回记忆">
          <section>
            <h2><UsersRound size={14} /> 关系摘要</h2>
            {relationship.data ? <>
              <strong>{relationshipStageLabels[relationship.data.relationship.stage]} · v{relationship.data.relationship.version}</strong>
              <p>{relationship.data.relationship.summary}</p>
              {relationship.data.relationship.boundaries.map((item) => <span key={item}>{item}</span>)}
            </> : <p>尚未形成稳定关系摘要。</p>}
          </section>
          <section>
            <h2><MemoryStick size={14} /> 本轮召回</h2>
            {!lastUserText && <p>发送消息后显示按当前配置召回的长期记忆。</p>}
            {recalledMemories.data?.items.map((item) => <article key={item.memory.id}>
              <strong>{memoryKindLabels[item.memory.kind]} · {item.score.toFixed(2)}</strong>
              <p>{item.memory.content}</p>
            </article>)}
            {lastUserText && !recalledMemories.isLoading && !recalledMemories.data?.items.length && <p>本轮没有匹配的长期记忆。</p>}
          </section>
        </aside>
      </section>
    </div>
  )
}
