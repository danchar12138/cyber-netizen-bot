import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  CircleStop,
  ImagePlus,
  LoaderCircle,
  Paperclip,
  SendHorizontal,
  Sparkles,
  UserRound,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  type ChatMessage,
  type ConversationEvent,
  cancelAgentRun,
  createConversation,
  getConversations,
  getDevelopmentIdentity,
  getMessages,
  sendChatMessage,
} from '../api'
import { applyConversationEvent } from '../chatEvents'

const messageStatusLabels: Record<ChatMessage['status'], string> = {
  received: '已接收',
  processing: '处理中',
  streaming: '正在输入',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失败',
}

export function ChatPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [connection, setConnection] = useState<'连接中' | '已连接' | '正在重连'>('连接中')
  const lastSequence = useRef(0)

  const identity = useQuery({ queryKey: ['chat-identity'], queryFn: getDevelopmentIdentity })
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: getConversations })
  const messageHistory = useQuery({
    queryKey: ['messages', selectedId],
    queryFn: () => getMessages(selectedId!),
    enabled: selectedId !== null,
  })

  useEffect(() => {
    if (!selectedId && conversations.data?.items[0]) {
      setSelectedId(conversations.data.items[0].id)
    }
  }, [conversations.data, selectedId])

  useEffect(() => {
    setMessages(messageHistory.data?.items ?? [])
  }, [messageHistory.data])

  useEffect(() => {
    if (!selectedId) return
    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(
        `${protocol}//${window.location.host}/api/v1/chat/conversations/${selectedId}/events?after=${lastSequence.current}`,
      )
      setConnection(lastSequence.current === 0 ? '连接中' : '正在重连')
      socket.onopen = () => setConnection('已连接')
      socket.onmessage = (frame) => {
        const event = JSON.parse(frame.data as string) as
          | ConversationEvent
          | { event_type: 'system.heartbeat'; last_sequence: number }
        if (!("sequence" in event)) return
        if (event.sequence <= lastSequence.current) return
        lastSequence.current = event.sequence
        setMessages((current) => applyConversationEvent(current, event))
        if (event.event_type === 'run.queued' || event.event_type === 'run.started') {
          if (event.run_id) setActiveRunId(event.run_id)
        }
        if (
          event.event_type === 'run.completed' ||
          event.event_type === 'run.cancelled' ||
          event.event_type === 'run.failed'
        ) {
          setActiveRunId(null)
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
  }, [selectedId])

  const createConversationMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: async (conversation) => {
      await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setSelectedId(conversation.id)
    },
  })

  const sendMessage = useMutation({
    mutationFn: (content: string) =>
      sendChatMessage(selectedId!, {
        client_message_id: crypto.randomUUID(),
        content,
      }),
    onSuccess: (accepted) => {
      setMessages((current) => {
        const withUser = applyConversationEvent(current, {
          schema_version: '1',
          event_id: crypto.randomUUID(),
          conversation_id: accepted.user_message.conversation_id,
          sequence: Number.MAX_SAFE_INTEGER - 1,
          event_type: 'message.created',
          occurred_at: accepted.user_message.created_at,
          run_id: accepted.run.id,
          message_id: accepted.user_message.id,
          payload: { ...accepted.user_message },
        })
        return applyConversationEvent(withUser, {
          schema_version: '1',
          event_id: crypto.randomUUID(),
          conversation_id: accepted.response_message.conversation_id,
          sequence: Number.MAX_SAFE_INTEGER,
          event_type: 'message.created',
          occurred_at: accepted.response_message.created_at,
          run_id: accepted.run.id,
          message_id: accepted.response_message.id,
          payload: { ...accepted.response_message },
        })
      })
      setActiveRunId(accepted.run.id)
      setDraft('')
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const cancelRun = useMutation({
    mutationFn: (runId: string) => cancelAgentRun(runId),
    onSuccess: () => setActiveRunId(null),
  })

  const selectedConversation = useMemo(
    () => conversations.data?.items.find((item) => item.id === selectedId),
    [conversations.data, selectedId],
  )
  const operationError =
    createConversationMutation.error ?? sendMessage.error ?? cancelRun.error

  const submit = () => {
    const content = draft.trim()
    if (!content || !selectedId || activeRunId || sendMessage.isPending) return
    sendMessage.mutate(content)
  }

  return (
    <div className="page chat-page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">内部对话工作台</p>
          <h1>内部对话</h1>
          <p>消息、Agent Run 与流式事件全部持久化，并支持断线后按事件序号恢复。</p>
        </div>
        <span className={`phase-tag connection ${connection === '已连接' ? 'online' : ''}`}>
          {connection}
        </span>
      </section>

      {operationError && <div className="notice error">{operationError.message}</div>}

      <section className="chat-workspace panel">
        <aside className="conversation-list">
          <button
            className="new-conversation"
            onClick={() => createConversationMutation.mutate()}
            disabled={createConversationMutation.isPending}
          >
            <Sparkles size={15} /> 新建会话
          </button>
          <p className="nav-label">最近会话</p>
          {conversations.data?.items.map((conversation) => (
            <button
              className={`conversation ${conversation.id === selectedId ? 'active' : ''}`}
              key={conversation.id}
              onClick={() => setSelectedId(conversation.id)}
            >
              <strong>{conversation.title}</strong>
              <span>{conversation.status === 'active' ? '进行中' : '已归档'}</span>
            </button>
          ))}
          {!conversations.isLoading && !conversations.data?.items.length && (
            <p className="empty-copy">还没有会话，创建一个开始聊天吧。</p>
          )}
        </aside>
        <div className="conversation-main">
          <div className="conversation-header">
            <div className="agent-avatar"><Bot size={20} /></div>
            <div>
              <strong>{identity.data?.agent_name ?? '赛博网友'}</strong>
              <span>{selectedConversation?.title ?? '请选择或创建会话'}</span>
            </div>
          </div>
          <div className={`message-stage ${messages.length ? 'has-messages' : ''}`}>
            {messageHistory.isLoading && <LoaderCircle className="spin" size={24} />}
            {!messageHistory.isLoading && messages.length === 0 && (
              <div className="chat-empty">
                <div className="welcome-orb"><Bot size={28} /></div>
                <h2>{selectedId ? '从一句真心话开始吧' : '对话工作台已经就位'}</h2>
                <p>
                  {selectedId
                    ? '当前使用无需密钥的本地流式 Provider，可直接验证完整链路。'
                    : '创建会话后即可验证消息持久化、流式响应、取消和断线恢复。'}
                </p>
              </div>
            )}
            {messages.map((message) => (
              <article className={`chat-message ${message.sender_type}`} key={message.id}>
                <div className="message-avatar">
                  {message.sender_type === 'agent' ? <Bot size={15} /> : <UserRound size={15} />}
                </div>
                <div className="message-bubble">
                  <div className="message-meta">
                    <strong>
                      {message.sender_type === 'agent'
                        ? identity.data?.agent_name ?? 'Agent'
                        : '我'}
                    </strong>
                    <span>{messageStatusLabels[message.status]}</span>
                  </div>
                  <p>{message.content || (message.status === 'processing' ? '正在思考…' : '…')}</p>
                </div>
              </article>
            ))}
          </div>
          <div className="composer-shell">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submit()
                }
              }}
              disabled={!selectedId}
              placeholder={
                selectedId
                  ? '输入消息，Enter 发送，Shift + Enter 换行'
                  : '请先创建会话'
              }
            />
            <div className="composer-actions">
              <div>
                <button disabled aria-label="添加附件"><Paperclip size={17} /></button>
                <button disabled aria-label="添加图片"><ImagePlus size={17} /></button>
              </div>
              {activeRunId ? (
                <button
                  className="stop-button"
                  aria-label="停止生成"
                  onClick={() => cancelRun.mutate(activeRunId)}
                  disabled={cancelRun.isPending}
                >
                  <CircleStop size={17} />
                </button>
              ) : (
                <button
                  className="send-button"
                  aria-label="发送消息"
                  onClick={submit}
                  disabled={!selectedId || !draft.trim() || sendMessage.isPending}
                >
                  <SendHorizontal size={16} />
                </button>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
