import { afterEach, describe, expect, it, vi } from 'vitest'

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = []

  onmessage: ((event: MessageEvent) => void) | null = null
  messages: unknown[] = []
  closed = false

  constructor(public readonly name: string) {
    FakeBroadcastChannel.instances.push(this)
  }

  postMessage(message: unknown) {
    this.messages.push(message)
  }

  close() {
    this.closed = true
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
  FakeBroadcastChannel.instances = []
})

describe('标签页同步', () => {
  it('广播查询失效并接收其他标签页的消息', async () => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const { broadcastQueryInvalidation, subscribeTabSync } = await import('./tabSync')
    const received: unknown[][] = []
    const unsubscribe = subscribeTabSync((message) => received.push(message.queryKey))
    const channel = FakeBroadcastChannel.instances[0]
    if (!channel) throw new Error('标签页同步通道没有创建')

    broadcastQueryInvalidation(['managed-entities', 'agents'])
    expect(channel.name).toBe('cnb-admin-sync')
    expect(channel.messages).toHaveLength(1)

    channel.onmessage?.({
      data: {
        sourceId: '另一个标签页',
        type: 'invalidate',
        queryKey: ['managed-entities', 'agents'],
        sentAt: Date.now(),
      },
    } as MessageEvent)
    expect(received).toEqual([['managed-entities', 'agents']])

    unsubscribe()
    expect(channel.closed).toBe(true)
  })
})
