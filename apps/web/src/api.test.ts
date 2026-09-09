import { describe, expect, it } from 'vitest'

import { formatConfigValue, formatConfigVersionStatus } from './api'

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
