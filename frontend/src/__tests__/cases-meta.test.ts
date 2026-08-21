import { describe, expect, it } from 'vitest'

import { ACTIONS, ASSERTION_TYPES, actionMeta, assertionMeta, defaultParams } from '@/api/cases'

describe('CR-09 动作/断言元数据契约', () => {
  it('覆盖 Agent Registry 全部 13 个动作', () => {
    const values = ACTIONS.map((a) => a.value).sort()
    expect(values).toEqual(
      [
        'launch_app',
        'close_app',
        'click',
        'input',
        'clear',
        'swipe',
        'scroll',
        'back',
        'sleep',
        'screenshot',
        'get_text',
        'get_attribute',
        'tap_coordinate',
      ].sort(),
    )
  })

  it('覆盖全部 8 个断言类型', () => {
    expect(ASSERTION_TYPES.map((a) => a.value).sort()).toEqual(
      [
        'element_exists',
        'text_equals',
        'text_contains',
        'text_not_contains',
        'attribute_equals',
        'attribute_contains',
        'value_equals',
        'regex_match',
      ].sort(),
    )
  })

  it('element_exists 需要元素选择器（修复原隐藏 bug）', () => {
    expect(assertionMeta('element_exists').needsElement).toBe(true)
  })

  it('regex_match 使用 pattern 字段（修复 expected 误用）', () => {
    const fields = assertionMeta('regex_match').fields
    expect(fields.some((f) => f.key === 'pattern')).toBe(true)
    expect(fields.some((f) => f.key === 'expected')).toBe(false)
  })

  it('defaultParams 只填充带默认值的字段', () => {
    const params = defaultParams(actionMeta('swipe').fields)
    expect(params.direction).toBe('up')
    expect(params.duration).toBe(500)
    const empty = defaultParams(actionMeta('back').fields)
    expect(Object.keys(empty)).toHaveLength(0)
  })

  it('launch_app 参数含 package/activity/no_reset', () => {
    const keys = actionMeta('launch_app').fields.map((f) => f.key)
    expect(keys).toEqual(['package', 'activity', 'no_reset'])
  })
})
