import { describe, expect, it } from 'vitest'

import {
  ACTIONS,
  ASSERTION_TYPES,
  actionMeta,
  assertionMeta,
  defaultParams,
  normalizeStep,
  type Step,
} from '@/api/cases'

describe('CR-09 动作/断言元数据契约', () => {
  it('覆盖 Agent Registry 全部 14 个动作', () => {
    const values = ACTIONS.map((a) => a.value).sort()
    expect(values).toEqual(
      [
        'launch_app',
        'close_app',
        'click',
        'input',
        'clear',
        'swipe',
        'swipe_to_find',
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

// Step 4：continue_on_failure 顶层契约 + click 等待秒数
describe('Step 4 步骤顶层 continue_on_failure 与 click 等待契约', () => {
  it('normalizeStep 保留顶层 continue_on_failure，缺失时归一化为 false', () => {
    const fresh: Step = { order: 1, action: 'click', continue_on_failure: true, params: {} }
    expect(normalizeStep(fresh).continue_on_failure).toBe(true)

    // 旧数据库数据缺顶层字段（历史快照，类型上不满足新版 Step 也属正常）
    const legacy = { order: 2, action: 'click', params: {} } as Step
    const norm = normalizeStep(legacy)
    expect(norm.continue_on_failure).toBe(false)
  })

  it('normalizeStep 剔除历史塞入 params 的 continue_on_failure', () => {
    const step = normalizeStep({
      order: 1,
      action: 'click',
      continue_on_failure: true,
      params: { continue_on_failure: true, wait_timeout: 5 },
    } as Step)
    expect(step.continue_on_failure).toBe(true)
    expect('continue_on_failure' in (step.params ?? {})).toBe(false)
    expect(step.params!.wait_timeout).toBe(5)
  })

  it('click 动作的等待秒数由 metadata 生成，默认 10 且在 0..300 内', () => {
    const meta = actionMeta('click')
    const field = meta.fields.find((f) => f.key === 'wait_timeout')!
    expect(field.default).toBe(10)
    expect(field.min).toBe(0)
    expect(field.max).toBe(300)
    const params = defaultParams(meta.fields)
    expect(params.wait_timeout).toBe(10)
    expect(typeof params.wait_timeout).toBe('number')
  })
})
