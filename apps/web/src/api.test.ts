import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  acknowledgeObservabilityAlert,
  clearObservabilityAlertDisposition,
  type ConfigPackageDocument,
  exportConfigPackage,
  formatConfigValue,
  formatConfigVersionStatus,
  getObservabilityAlertLifecycles,
  getObservabilityAlertLifecycleMetrics,
  importConfigPackage,
  setApiAccessToken,
  suppressObservabilityAlert,
} from './api'
import { setSelectedAgentId } from './agentSelection'

afterEach(() => {
  setApiAccessToken(null)
  setSelectedAgentId(null)
  vi.unstubAllGlobals()
})

describe('formatConfigValue', () => {
  it('为中文管理界面格式化布尔值', () => {
    expect(formatConfigValue(true)).toBe('开启')
    expect(formatConfigValue(false)).toBe('关闭')
  })

  it('不会混淆未设置值与空字符串', () => {
    expect(formatConfigValue(null)).toBe('未设置')
    expect(formatConfigValue('')).toBe('')
  })
})

describe('formatConfigVersionStatus', () => {
  it('以中文展示所有配置版本状态', () => {
    expect(formatConfigVersionStatus('draft')).toBe('草稿')
    expect(formatConfigVersionStatus('published')).toBe('已发布')
    expect(formatConfigVersionStatus('superseded')).toBe('已被替代')
  })
})

describe('通用告警客户端', () => {
  it('完整传递生命周期组合筛选参数', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertLifecycles({
      status: 'active',
      source_type: 'model_runtime',
      severity: 'critical',
      minimum_duration_minutes: 30,
    })

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/observability/alert-lifecycles')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      status: 'active',
      source_type: 'model_runtime',
      severity: 'critical',
      minimum_duration_minutes: '30',
      limit: '100',
    })
  })

  it('使用专用路径和显式确认请求体执行三种处置', async () => {
    const lifecycleId = '11111111-1111-4111-8111-111111111111'
    const response = {
      lifecycle_id: lifecycleId,
      code: 'model_failure_rate',
      source_type: 'model_runtime',
      source_key: 'openai:gpt-5',
      status: 'acknowledged',
      reason: '值班人员正在处理',
      expires_at: null,
      updated_at: '2026-09-14T04:00:00Z',
    }
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(
      JSON.stringify(response),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    vi.stubGlobal('fetch', fetchMock)

    await acknowledgeObservabilityAlert(lifecycleId, {
      confirmed: true,
      reason: '值班人员正在处理',
    })
    await suppressObservabilityAlert(lifecycleId, {
      confirmed: true,
      reason: '计划内维护',
      expires_at: '2026-09-14T06:00:00Z',
    })
    await clearObservabilityAlertDisposition(lifecycleId, { confirmed: true })

    const requests = fetchMock.mock.calls.map((call) => call[0] as Request)
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      `/api/v1/observability/alert-lifecycles/${lifecycleId}/acknowledge`,
      `/api/v1/observability/alert-lifecycles/${lifecycleId}/suppress`,
      `/api/v1/observability/alert-lifecycles/${lifecycleId}/clear-disposition`,
    ])
    expect(await requests[0]!.json()).toEqual({ confirmed: true, reason: '值班人员正在处理' })
    expect(await requests[1]!.json()).toEqual({
      confirmed: true,
      reason: '计划内维护',
      expires_at: '2026-09-14T06:00:00Z',
    })
    expect(await requests[2]!.json()).toEqual({ confirmed: true })
  })

  it('使用通用告警专用路径传递趋势筛选', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      window_started_at: '2026-09-14T12:00:00Z',
      window_ended_at: '2026-09-15T12:00:00Z',
      active: 0,
      opened: 0,
      resolved: 0,
      escalated: 0,
      mean_recovery_seconds: 0,
      p95_recovery_seconds: 0,
      sources: [],
      trend: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertLifecycleMetrics({
      window_minutes: 720,
      bucket_minutes: 30,
      source_type: 'api',
      severity: 'warning',
    })

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/observability/alert-lifecycles/metrics')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      window_minutes: '720',
      bucket_minutes: '30',
      source_type: 'api',
      severity: 'warning',
    })
  })
})

describe('配置包客户端', () => {
  it('使用专用安全导入导出端点', async () => {
    const packageDocument: ConfigPackageDocument = {
      format: 'cnb-runtime-configuration',
      schema_version: '1',
      source: {
        version: 3,
        status: 'published',
        note: '中文配置基线',
        created_at: '2026-09-10T00:00:00Z',
        published_at: '2026-09-10T00:01:00Z',
      },
      values: [{
        key: 'memory.recall.limit',
        scope_type: 'system',
        scope_id: null,
        value: 16,
      }],
    }
    const imported = {
      id: '00000000-0000-0000-0000-000000000001',
      version: 4,
      status: 'draft',
      note: '从配置包 v3 导入：中文配置基线',
      created_at: '2026-09-10T00:02:00Z',
      published_at: null,
      values: packageDocument.values,
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(packageDocument), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(imported), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    expect(await exportConfigPackage('version-id')).toEqual(packageDocument)
    expect(await importConfigPackage(packageDocument)).toEqual(imported)
    const exportRequest = fetchMock.mock.calls[0]?.[0] as Request
    const importRequest = fetchMock.mock.calls[1]?.[0] as Request
    expect(exportRequest.url).toBe('http://localhost:3000/api/v1/configuration/versions/version-id/export')
    expect(exportRequest.method).toBe('GET')
    expect(exportRequest.headers.get('Accept')).toBe('application/json')
    expect(importRequest.url).toBe('http://localhost:3000/api/v1/configuration/imports')
    expect(importRequest.method).toBe('POST')
    expect(importRequest.headers.get('Content-Type')).toBe('application/json')
    expect(await importRequest.json()).toEqual(packageDocument)
  })

  it('为生成客户端注入仅驻留内存的访问令牌并转换安全错误', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      schema_version: '1',
      error: { code: 'forbidden', message: '没有查看配置包的权限', request_id: 'request-id' },
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    setApiAccessToken('signed-access-token')
    setSelectedAgentId('33333333-3333-4333-8333-333333333333')

    await expect(exportConfigPackage('version-id')).rejects.toThrow('没有查看配置包的权限')

    const request = fetchMock.mock.calls[0]?.[0] as Request
    expect(request.headers.get('Authorization')).toBe('Bearer signed-access-token')
    expect(request.headers.get('X-CNB-Agent-ID')).toBe(
      '33333333-3333-4333-8333-333333333333',
    )
  })

  it('将网络故障转换为自然中文提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(exportConfigPackage('version-id')).rejects.toThrow(
      '无法连接 API 服务，请检查网络或服务状态',
    )
  })
})
