import { client } from './generated/client.gen'

export const apiClient = client

interface ApiErrorEnvelope {
  error?: {
    message?: string
  }
}

export class ApiClientError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message)
    this.name = 'ApiClientError'
  }
}

let accessToken: string | null = null

client.setConfig({ baseUrl: globalThis.location?.origin ?? 'http://localhost' })

client.interceptors.request.use((request) => {
  const headers = new Headers(request.headers)
  headers.set('Accept', 'application/json')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  else headers.delete('Authorization')
  return new Request(request, { headers })
})

client.interceptors.error.use((error, response) => {
  const payload = error as ApiErrorEnvelope | null
  const message = payload?.error?.message
  if (message) return new ApiClientError(message, response?.status)
  if (error instanceof Error && error.name === 'AbortError') {
    return new ApiClientError('请求已取消', response?.status)
  }
  if (error instanceof Error && response) {
    return new ApiClientError(error.message || `请求失败，状态码 ${response.status}`, response.status)
  }
  return new ApiClientError(
    response ? `请求失败，状态码 ${response.status}` : '无法连接 API 服务，请检查网络或服务状态',
    response?.status,
  )
})

export function setGeneratedApiAccessToken(token: string | null): void {
  accessToken = token
}

export function getGeneratedApiAccessToken(): string | null {
  return accessToken
}
