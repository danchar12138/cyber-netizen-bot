import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'cnb-selected-agent-id'

type AgentSelectionListener = () => void

const listeners = new Set<AgentSelectionListener>()

function readStoredAgentId(): string | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

let selectedAgentId = readStoredAgentId()
let listeningToStorage = false

function emitSelectionChange(): void {
  for (const listener of listeners) listener()
}

function handleStorage(event: StorageEvent): void {
  if (event.key !== STORAGE_KEY || event.newValue === selectedAgentId) return
  selectedAgentId = event.newValue
  emitSelectionChange()
}

function ensureStorageListener(): void {
  if (listeningToStorage || typeof window === 'undefined') return
  window.addEventListener('storage', handleStorage)
  listeningToStorage = true
}

export function getSelectedAgentId(): string | null {
  return selectedAgentId
}

export function setSelectedAgentId(agentId: string | null): void {
  const normalized = agentId?.trim() || null
  if (normalized === selectedAgentId) return
  selectedAgentId = normalized
  if (typeof window !== 'undefined') {
    try {
      if (normalized) window.localStorage.setItem(STORAGE_KEY, normalized)
      else window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 禁用本地存储时选择仍在当前标签页内生效。
    }
  }
  emitSelectionChange()
}

export function subscribeAgentSelection(listener: AgentSelectionListener): () => void {
  listeners.add(listener)
  ensureStorageListener()
  return () => listeners.delete(listener)
}

export function useSelectedAgentId(): string | null {
  return useSyncExternalStore(subscribeAgentSelection, getSelectedAgentId, () => null)
}
