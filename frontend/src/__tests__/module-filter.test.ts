import { describe, expect, it } from 'vitest'

import {
  moduleFilterParams,
  moduleKeyFromId,
  moduleQuery,
  parseModuleKey,
} from '@/utils/moduleFilter'
import { caseListQuery } from '@/utils/caseModuleNavigation'

describe('模块筛选三态', () => {
  it('全部 → 不带模块参数', () => {
    expect(moduleFilterParams('all')).toEqual({})
    expect(moduleQuery('all')).toEqual({})
  })

  it('未分组 → ungrouped=true（不能用 module_id=null，axios 会丢弃该参数）', () => {
    expect(moduleFilterParams('none')).toEqual({ ungrouped: true })
    expect(moduleFilterParams('none')).not.toHaveProperty('module_id')
    expect(moduleQuery('none')).toEqual({ module: 'none' })
  })

  it('具体模块 → module_id 数字', () => {
    expect(moduleFilterParams('12')).toEqual({ module_id: 12 })
    expect(moduleQuery('12')).toEqual({ module: '12' })
  })

  it('moduleKeyFromId 区分 null（未分组）与具体 id', () => {
    expect(moduleKeyFromId(null)).toBe('none')
    expect(moduleKeyFromId(undefined)).toBe('none')
    expect(moduleKeyFromId(7)).toBe('7')
  })

  it('parseModuleKey 对非法值回落到 all', () => {
    expect(parseModuleKey('none')).toBe('none')
    expect(parseModuleKey('9')).toBe('9')
    expect(parseModuleKey('invalid')).toBe('all')
    expect(parseModuleKey('0')).toBe('all')
    expect(parseModuleKey('-3')).toBe('all')
    expect(parseModuleKey(undefined)).toBe('all')
    expect(parseModuleKey(['none', '9'])).toBe('none')
  })

  it('列表返回上下文时保留三态', () => {
    expect(caseListQuery('none', 2)).toEqual({ module: 'none', page: '2' })
    expect(caseListQuery('all', 2)).toEqual({ page: '2' })
  })
})
