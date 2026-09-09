export interface ComponentHealth {
  name: string
  status: 'healthy' | 'ready' | 'degraded' | 'not_checked' | 'not_configured'
  detail?: string | null
}

export interface SystemOverview {
  environment: string
  version: string
  active_agents: number
  active_conversations: number
  pending_jobs: number
  configuration_definitions: number
  components: ComponentHealth[]
}

export type ConfigValue = string | number | boolean | null | ConfigValue[] | {
  [key: string]: ConfigValue
}

export interface ConfigDefinition {
  key: string
  section: string
  label: string
  description: string
  value_kind: 'string' | 'integer' | 'number' | 'boolean' | 'string_list' | 'secret'
  default: ConfigValue
  scopes: string[]
  secret: boolean
  hot_reload: boolean
  minimum?: number | null
  maximum?: number | null
  options: string[]
}

interface ConfigRegistry {
  schema_version: string
  definitions: ConfigDefinition[]
}

export type ConfigVersionStatus = 'draft' | 'published' | 'superseded'

export interface ConfigVersionValue {
  key: string
  scope_type: string
  scope_id: string | null
  value: ConfigValue
}

export interface ConfigVersion {
  id: string
  version: number
  status: ConfigVersionStatus
  note: string | null
  created_at: string
  published_at: string | null
  values: ConfigVersionValue[]
}

interface ConfigVersionList {
  versions: ConfigVersion[]
}

interface ConfigDraftCommand {
  note: string | null
  values: Array<{
    key: string
    scope_type: 'system'
    scope_id: null
    value: ConfigValue
  }>
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(payload?.detail ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const getSystemOverview = () => getJson<SystemOverview>('/api/v1/system/overview')

export const getConfigRegistry = () =>
  getJson<ConfigRegistry>('/api/v1/configuration/definitions')

export const getConfigVersions = () =>
  getJson<ConfigVersionList>('/api/v1/configuration/versions')

export const createConfigDraft = (command: ConfigDraftCommand) =>
  postJson<ConfigVersion>('/api/v1/configuration/drafts', command)

export const publishConfigVersion = (versionId: string) =>
  postJson<ConfigVersion>(`/api/v1/configuration/versions/${versionId}/publish`)

export const rollbackConfigVersion = (versionId: string) =>
  postJson<ConfigVersion>(`/api/v1/configuration/versions/${versionId}/rollback`)

export function formatConfigValue(value: ConfigValue): string {
  if (value === null) return '未设置'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const configVersionStatusLabels: Record<ConfigVersionStatus, string> = {
  draft: '草稿',
  published: '已发布',
  superseded: '已被替代',
}

export function formatConfigVersionStatus(status: ConfigVersionStatus): string {
  return configVersionStatusLabels[status]
}
