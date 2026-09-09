import { describe, expect, it } from 'vitest'

import { formatConfigValue } from './api'

describe('formatConfigValue', () => {
  it('formats booleans for the Chinese administration UI', () => {
    expect(formatConfigValue(true)).toBe('开启')
    expect(formatConfigValue(false)).toBe('关闭')
  })

  it('does not confuse an unset value with an empty string', () => {
    expect(formatConfigValue(null)).toBe('未设置')
    expect(formatConfigValue('')).toBe('')
  })
})

