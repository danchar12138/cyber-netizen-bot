import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  type ConfigPackageDocument,
  exportConfigPackage,
  formatConfigValue,
  formatConfigVersionStatus,
  importConfigPackage,
} from './api'

afterEach(() => {
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
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(packageDocument) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(imported) })
    vi.stubGlobal('fetch', fetchMock)

    expect(await exportConfigPackage('version-id')).toEqual(packageDocument)
    expect(await importConfigPackage(packageDocument)).toEqual(imported)
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/v1/configuration/versions/version-id/export',
      { headers: { Accept: 'application/json' } },
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/v1/configuration/imports',
      {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(packageDocument),
      },
    )
  })
})
