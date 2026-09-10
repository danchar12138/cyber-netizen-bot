import type { ChatMessage, ConversationEvent } from './api'

function isMessagePayload(
  payload: Record<string, unknown>,
): payload is Record<string, unknown> & ChatMessage {
  return (
    typeof payload.id === 'string' &&
    typeof payload.conversation_id === 'string' &&
    typeof payload.sender_type === 'string' &&
    typeof payload.content === 'string' &&
    typeof payload.status === 'string' &&
    typeof payload.created_at === 'string' &&
    typeof payload.updated_at === 'string'
  )
}

function upsert(messages: ChatMessage[], incoming: ChatMessage): ChatMessage[] {
  const incomingHasParts = Array.isArray(incoming.parts)
  const normalizedIncoming = withFallbackPart(incoming)
  const existingIndex = messages.findIndex((item) => item.id === incoming.id)
  if (existingIndex < 0) {
    return [...messages, normalizedIncoming].sort((left, right) =>
      left.created_at.localeCompare(right.created_at),
    )
  }
  return messages.map((item) => {
    if (item.id !== incoming.id) return item
    if (
      incoming.status === 'processing' &&
      (item.status === 'streaming' ||
        item.status === 'completed' ||
        item.status === 'suppressed')
    ) {
      return item
    }
    return incomingHasParts
      ? normalizedIncoming
      : {
          ...normalizedIncoming,
          parts: item.parts.map((part) =>
            part.position === 0 && (part.kind === 'text' || part.kind === 'markdown')
              ? { ...part, text: normalizedIncoming.content, updated_at: normalizedIncoming.updated_at }
              : part,
          ),
        }
  })
}

function withFallbackPart(message: ChatMessage): ChatMessage {
  if (Array.isArray(message.parts)) return message
  return {
    ...message,
    parts: [{
      id: `legacy-${message.id}`,
      position: 0,
      kind: 'markdown',
      text: message.content,
      attachment_id: null,
      content_type: null,
      file_name: null,
      size_bytes: null,
      sha256: null,
      alt_text: null,
      created_at: message.created_at,
      updated_at: message.updated_at,
    }],
  }
}

export function applyConversationEvent(
  messages: ChatMessage[],
  event: ConversationEvent,
): ChatMessage[] {
  if (
    (event.event_type === 'message.created' ||
      event.event_type === 'message.completed' ||
      event.event_type === 'message.suppressed') &&
    isMessagePayload(event.payload)
  ) {
    return upsert(messages, event.payload)
  }
  if (event.event_type === 'message.delta') {
    const messageId = event.payload.message_id
    const delta = event.payload.delta
    if (typeof messageId !== 'string' || typeof delta !== 'string') return messages
    return messages.map((item) =>
      item.id === messageId
        ? {
            ...item,
            content: item.content + delta,
            status: 'streaming',
            parts: item.parts.map((part) =>
              part.position === 0 && (part.kind === 'text' || part.kind === 'markdown')
                ? { ...part, text: (part.text ?? '') + delta, updated_at: event.occurred_at }
                : part,
            ),
          }
        : item,
    )
  }
  if (event.event_type === 'run.cancelled' || event.event_type === 'run.failed') {
    const messageId = event.payload.response_message_id
    if (typeof messageId !== 'string') return messages
    const status = event.event_type === 'run.cancelled' ? 'cancelled' : 'failed'
    return messages.map((item) => (item.id === messageId ? { ...item, status } : item))
  }
  return messages
}
