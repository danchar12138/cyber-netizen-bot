import { describe, expect, it } from 'vitest'

import type { ChatMessage, ConversationEvent } from './api'
import { applyConversationEvent } from './chatEvents'

const assistant: ChatMessage = {
  id: 'message-1',
  conversation_id: 'conversation-1',
  sender_type: 'agent',
  sender_id: 'agent-1',
  content: '',
  status: 'processing',
  client_message_id: null,
  created_at: '2026-09-09T00:00:00Z',
  updated_at: '2026-09-09T00:00:00Z',
  edited_from_id: null,
  parts: [{
    id: 'part-1',
    position: 0,
    kind: 'markdown',
    text: '',
    attachment_id: null,
    content_type: null,
    file_name: null,
    size_bytes: null,
    sha256: null,
    alt_text: null,
    created_at: '2026-09-09T00:00:00Z',
    updated_at: '2026-09-09T00:00:00Z',
  }],
}

function event(eventType: string, payload: Record<string, unknown>): ConversationEvent {
  return {
    schema_version: '1',
    event_id: crypto.randomUUID(),
    conversation_id: 'conversation-1',
    sequence: 1,
    event_type: eventType,
    occurred_at: '2026-09-09T00:00:00Z',
    run_id: 'run-1',
    message_id: 'message-1',
    payload,
  }
}

describe('applyConversationEvent', () => {
  it('按流式增量更新已有消息', () => {
    const next = applyConversationEvent(
      [assistant],
      event('message.delta', { message_id: assistant.id, delta: '你好' }),
    )

    expect(next[0]!.content).toBe('你好')
    expect(next[0]!.parts[0]!.text).toBe('你好')
    expect(next[0]!.status).toBe('streaming')
  })

  it('完成事件以服务端最终快照覆盖本地内容', () => {
    const completed = { ...assistant, content: '完整回复', status: 'completed' as const }
    const next = applyConversationEvent(
      [{ ...assistant, content: '不完整' }],
      event('message.completed', { ...completed }),
    )

    expect(next).toEqual([completed])
  })

  it('较晚到达的接收响应不会覆盖已完成的流式消息', () => {
    const completed = { ...assistant, content: '完整回复', status: 'completed' as const }
    const next = applyConversationEvent(
      [completed],
      event('message.created', { ...assistant }),
    )

    expect(next).toEqual([completed])
  })

  it('不回复事件会终结占位消息且不会被较晚响应覆盖', () => {
    const suppressed = { ...assistant, status: 'suppressed' as const }
    const applied = applyConversationEvent(
      [assistant],
      event('message.suppressed', { ...suppressed }),
    )
    const replayed = applyConversationEvent(
      applied,
      event('message.created', { ...assistant }),
    )

    expect(replayed).toEqual([suppressed])
  })

  it('为升级前的不含内容块事件补齐兼容 Markdown 块', () => {
    const legacy: Record<string, unknown> = { ...assistant, content: '旧消息' }
    delete legacy.parts
    const next = applyConversationEvent([], event('message.created', legacy))

    expect(next[0]!.parts).toHaveLength(1)
    expect(next[0]!.parts[0]!.kind).toBe('markdown')
    expect(next[0]!.parts[0]!.text).toBe('旧消息')
  })
})
