import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  type ConfigPackageDocument,
  exportConfigPackage,
  formatConfigValue,
  formatConfigVersionStatus,
  importConfigPackage,
  setApiAccessToken,
} from './api'

afterEach(() => {
  setApiAccessToken(null)
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

    await expect(exportConfigPackage('version-id')).rejects.toThrow('没有查看配置包的权限')

    const request = fetchMock.mock.calls[0]?.[0] as Request
    expect(request.headers.get('Authorization')).toBe('Bearer signed-access-token')
  })

  it('将网络故障转换为自然中文提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(exportConfigPackage('version-id')).rejects.toThrow(
      '无法连接 API 服务，请检查网络或服务状态',
    )
  })
})
