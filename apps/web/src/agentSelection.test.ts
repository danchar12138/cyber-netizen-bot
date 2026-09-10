import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  window.localStorage.clear()
  vi.resetModules()
})

describe('当前 Agent 选择', () => {
  it('持久化选择并通知当前标签页订阅者', async () => {
    const selection = await import('./agentSelection')
    const listener = vi.fn()
    const unsubscribe = selection.subscribeAgentSelection(listener)

    selection.setSelectedAgentId('33333333-3333-4333-8333-333333333333')

    expect(selection.getSelectedAgentId()).toBe('33333333-3333-4333-8333-333333333333')
    expect(window.localStorage.getItem('cnb-selected-agent-id')).toBe(
      '33333333-3333-4333-8333-333333333333',
    )
    expect(listener).toHaveBeenCalledOnce()
    unsubscribe()
  })

  it('接收其他标签页写入的 Agent 选择', async () => {
    const selection = await import('./agentSelection')
    const listener = vi.fn()
    selection.subscribeAgentSelection(listener)

    window.dispatchEvent(new StorageEvent('storage', {
      key: 'cnb-selected-agent-id',
      newValue: '44444444-4444-4444-8444-444444444444',
    }))

    expect(selection.getSelectedAgentId()).toBe('44444444-4444-4444-8444-444444444444')
    expect(listener).toHaveBeenCalledOnce()
  })
})
