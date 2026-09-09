import type { QueryClient } from '@tanstack/react-query'

const CHANNEL_NAME = 'cnb-admin-sync'
const STORAGE_KEY = 'cnb-admin-sync-message'
const SOURCE_ID = crypto.randomUUID()

export interface TabSyncMessage {
  sourceId: string
  type: 'invalidate'
  queryKey: unknown[]
  sentAt: number
}

type TabSyncListener = (message: TabSyncMessage) => void

const listeners = new Set<TabSyncListener>()
let channel: BroadcastChannel | null = null
let storageListener: ((event: StorageEvent) => void) | null = null

function parseMessage(value: string | null): TabSyncMessage | null {
  if (!value) return null
  try {
    const message = JSON.parse(value) as Partial<TabSyncMessage>
    if (
      message.sourceId === SOURCE_ID
      || message.type !== 'invalidate'
      || !Array.isArray(message.queryKey)
      || typeof message.sentAt !== 'number'
    ) return null
    return message as TabSyncMessage
  } catch {
    return null
  }
}

function dispatch(message: TabSyncMessage) {
  for (const listener of listeners) listener(message)
}

function ensureTransport() {
  if (typeof window === 'undefined') return
  if (channel === null && typeof BroadcastChannel !== 'undefined') {
    channel = new BroadcastChannel(CHANNEL_NAME)
    channel.onmessage = (event: MessageEvent<TabSyncMessage>) => {
      const message = parseMessage(JSON.stringify(event.data))
      if (message) dispatch(message)
    }
  }
  if (storageListener === null && channel === null) {
    storageListener = (event) => {
      const message = parseMessage(event.key === STORAGE_KEY ? event.newValue : null)
      if (message) dispatch(message)
    }
    window.addEventListener('storage', storageListener)
  }
}

export function subscribeTabSync(listener: TabSyncListener): () => void {
  listeners.add(listener)
  ensureTransport()
  return () => {
    listeners.delete(listener)
    if (listeners.size > 0 || typeof window === 'undefined') return
    channel?.close()
    channel = null
    if (storageListener) window.removeEventListener('storage', storageListener)
    storageListener = null
  }
}

export function broadcastQueryInvalidation(queryKey: readonly unknown[]): void {
  ensureTransport()
  const message: TabSyncMessage = {
    sourceId: SOURCE_ID,
    type: 'invalidate',
    queryKey: [...queryKey],
    sentAt: Date.now(),
  }
  if (channel) {
    channel.postMessage(message)
    return
  }
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(message))
      window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 隐私模式或禁用存储时，当前 Tab 仍可继续工作。
    }
  }
}

export function invalidateAcrossTabs(
  queryClient: QueryClient,
  queryKey: readonly unknown[],
): Promise<void> {
  broadcastQueryInvalidation(queryKey)
  return queryClient.invalidateQueries({ queryKey }).then(() => undefined)
}
