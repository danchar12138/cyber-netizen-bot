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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const getSystemOverview = () => getJson<SystemOverview>('/api/v1/system/overview')

export const getConfigRegistry = () =>
  getJson<ConfigRegistry>('/api/v1/configuration/definitions')

export function formatConfigValue(value: ConfigValue): string {
  if (value === null) return '未设置'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

