import { describe, expect, it } from 'vitest'

import {
  ACTIONS,
  ASSERTION_TYPES,
  actionMeta,
  assertionMeta,
  defaultParams,
  normalizeStep,
  validateActionParams,
  validateStep,
  type Step,
} from '@/api/cases'

describe('CR-09 动作/断言元数据契约', () => {
  it('覆盖 Agent Registry 全部 18 个动作', () => {
    const values = ACTIONS.map((a) => a.value).sort()
    expect(values).toEqual(
      [
        'launch_app',
        'close_app',
        'click',
        'input',
        'clear',
        'set_checked',
        'swipe',
        'swipe_to_find',
        'swipe_in_element',
        'swipe_in_region',
        'swipe_in_element_find_text_click',
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

  it('覆盖全部 9 个断言类型', () => {
    expect(ASSERTION_TYPES.map((a) => a.value).sort()).toEqual(
      [
        'element_exists',
        'checked',
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

  it('set_checked 与 checked 使用复选框状态字段', () => {
    const action = actionMeta('set_checked')
    const assertion = assertionMeta('checked')
    expect(action.needsElement).toBe(true)
    expect(action.elementLabel).toBe('复选框')
    expect(defaultParams(action.fields)).toEqual({ checked: true })
    expect(assertion.needsElement).toBe(true)
    expect(defaultParams(assertion.fields)).toEqual({ checked: true })
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

  it('两个限定范围滑动动作的元数据和默认值正确', () => {
    const elementSwipe = actionMeta('swipe_in_element')
    expect(elementSwipe.needsElement).toBe(true)
    expect(defaultParams(elementSwipe.fields)).toMatchObject({ direction: 'up', percent: 0.3, wait_timeout: 10 })

    const regionSwipe = actionMeta('swipe_in_region')
    expect(regionSwipe.needsElement).toBe(false)
    expect(regionSwipe.constraints).toEqual([
      { type: 'percent_region', left: 'left_percent', top: 'top_percent', width: 'width_percent', height: 'height_percent' },
    ])
  })

  it('区域内滑动会在保存前校验区域边界', () => {
    expect(validateActionParams('swipe_in_region', {
      left_percent: 60,
      top_percent: 0,
      width_percent: 50,
      height_percent: 100,
      percent: 0.3,
    })).toBe('左边界(%) + 宽度(%) 不能大于 100')
    expect(validateActionParams('swipe_in_region', {
      left_percent: 10,
      top_percent: 20,
      width_percent: 50,
      height_percent: 60,
      percent: 0.3,
    })).toBeNull()
  })

  it('列表内滑动查找文字并点击 的元数据和默认值正确', () => {
    const meta = actionMeta('swipe_in_element_find_text_click')
    expect(meta.needsElement).toBe(true)
    expect(meta.elementLabel).toBe('列表控件')
    expect(defaultParams(meta.fields)).toMatchObject({
      match_mode: 'equals',
      preferred_direction: 'up',
      max_swipes_per_direction: 8,
      percent: 0.3,
      container_wait_timeout: 10,
      settle_ms: 300,
    })
    const textField = meta.fields.find((f) => f.key === 'target_text')!
    expect(textField.required).toBe(true)
    expect(textField.minLength).toBe(1)
    expect(meta.fields.some((f) => f.key === 'viewport_mode')).toBe(false)
    expect(meta.fields.some((f) => f.key === 'viewport_element_id')).toBe(false)
  })

  it('列表内滑动查找文字并点击 保存前校验参数范围', () => {
    expect(validateActionParams('swipe_in_element_find_text_click', {
      target_text: '系统时间',
      max_swipes_per_direction: 51,
    })).toBe('“每方向最大滑动次数”不能大于 50')
    expect(validateActionParams('swipe_in_element_find_text_click', {
      target_text: '   ',
    })).toBe('“目标文字”不能为空')
    expect(validateActionParams('swipe_in_element_find_text_click', {
      target_text: '系统时间',
      percent: 0.04,
    })).toBe('“滑动比例（0.05～0.95）”不能小于 0.05')
  })

  it('validateStep 覆盖 needsElement 缺元素校验', () => {
    expect(validateStep({ order: 1, action: 'swipe_in_element_find_text_click', element_id: null, params: { target_text: 'x' }, continue_on_failure: false })).toBe('请选择“列表控件”')
    expect(validateStep({ order: 1, action: 'click', element_id: null, params: {}, continue_on_failure: false })).toBe('请选择“元素”')
    expect(validateStep({ order: 1, action: 'click', element_id: 1, params: {}, continue_on_failure: false })).toBeNull()
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
    expect(norm.phase).toBe('main')
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
