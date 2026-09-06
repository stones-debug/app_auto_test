import { describe, expect, it } from 'vitest'

import {
  ACTIONS,
  ASSERTION_TYPES,
  actionMeta,
  assertionMeta,
  defaultParams,
  normalizeActionNode,
  normalizeStep,
  validateActionParams,
  validateStep,
  type ActionNode,
  type Step,
} from '@/api/cases'

describe('CR-09 动作/断言元数据契约', () => {
  it('覆盖 Agent Registry 全部 19 个动作', () => {
    const values = ACTIONS.map((a) => a.value).sort()
    expect(values).toEqual(
      [
        'launch_app',
        'close_app',
        'click',
        'input',
        'clear',
        'set_checked',
        'set_slider_value',
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

  it('覆盖全部 11 个断言类型', () => {
    expect(ASSERTION_TYPES.map((a) => a.value).sort()).toEqual(
      [
        'element_exists',
        'checked',
        'text_equals',
        'text_not_equals',
        'text_contains',
        'text_not_contains',
        'attribute_equals',
        'attribute_contains',
        'value_equals',
        'regex_match',
        'number_compare',
      ].sort(),
    )
  })

  it('数字比较使用比较符与可含变量的文本目标值', () => {
    const assertion = assertionMeta('number_compare')
    expect(assertion.label).toBe('数字比较')
    expect(assertion.needsElement).toBe(true)
    expect(assertion.fields).toMatchObject([
      {
        key: 'operator',
        type: 'select',
        default: '>',
        options: [
          { value: '>', label: '大于（>）' },
          { value: '>=', label: '大于等于（>=）' },
          { value: '<', label: '小于（<）' },
          { value: '<=', label: '小于等于（<=）' },
          { value: '==', label: '等于（==）' },
          { value: '!=', label: '不等于（!=）' },
        ],
      },
      { key: 'expected', type: 'text', required: true },
    ])
    expect(defaultParams(assertion.fields)).toEqual({ operator: '>' })
  })

  it('element_exists 需要元素选择器（修复原隐藏 bug）', () => {
    expect(assertionMeta('element_exists').needsElement).toBe(true)
  })

  it('文本不等于与文本等于使用对称参数', () => {
    const assertion = assertionMeta('text_not_equals')
    expect(assertion.label).toBe('文本不等于')
    expect(assertion.needsElement).toBe(true)
    expect(assertion.fields).toMatchObject([
      { key: 'expected', required: true },
      { key: 'trim', default: false },
    ])
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

  it('滑动查找元素展示可控距离与稳定等待参数', () => {
    const meta = actionMeta('swipe_to_find')
    expect(meta.needsElement).toBe(true)
    expect(defaultParams(meta.fields)).toEqual({
      direction: 'up',
      max_swipes: 5,
      wait_timeout: 2,
      percent: 0.2,
      duration: 500,
      settle_ms: 500,
    })
    expect(meta.fields.find((field) => field.key === 'percent')).toMatchObject({
      min: 0.05,
      max: 0.95,
      default: 0.2,
    })
    expect(meta.fields.find((field) => field.key === 'settle_ms')).toMatchObject({
      min: 0,
      max: 5000,
      default: 500,
    })
    expect(validateActionParams('swipe_to_find', { percent: 0.04 })).toBe(
      '“滑动距离比例（0.05～0.95）”不能小于 0.05',
    )
    expect(validateActionParams('swipe_to_find', { settle_ms: 5001 })).toBe(
      '“滑动后稳定等待(ms)”不能大于 5000',
    )
  })

  it('设置滑块数值仅展示新闭环参数并校验目标范围', () => {
    const slider = actionMeta('set_slider_value')
    expect(slider.needsElement).toBe(true)
    expect(slider.elementLabel).toBe('滑块按钮（文本为当前值）')
    expect(slider.fields.map((field) => field.key)).toEqual([
      'min_value', 'max_value', 'target_value', 'duration_ms', 'settle_ms',
      'tolerance', 'max_adjustments', 'wait_timeout',
    ])
    expect(defaultParams(slider.fields)).toEqual({
      duration_ms: 300,
      settle_ms: 300,
      tolerance: 0,
      max_adjustments: 3,
      wait_timeout: 10,
    })
    expect(slider.fields.some((field) => field.key === 'value_element_id')).toBe(false)
    expect(slider.fields.some((field) => field.key === 'verify_value')).toBe(false)
    expect(validateActionParams('set_slider_value', {
      min_value: 52,
      max_value: 100,
      target_value: 40,
    })).toBe('目标值必须在最小值与最大值之间')
    expect(validateActionParams('set_slider_value', {
      min_value: 10,
      max_value: 10,
      target_value: 10,
    })).toBe('最小值必须小于最大值')
    expect(validateActionParams('set_slider_value', {
      min_value: 52,
      max_value: 100,
      target_value: 80,
    })).toBeNull()
    expect(validateActionParams('set_slider_value', {
      min_value: 52,
      max_value: 100,
      target_value: 80,
    })).toBeNull()
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

describe('Step 26 加载旧动作参数时回填 registry 默认值', () => {
  it('统一流动作节点回填 swipe_to_find 的新增参数', () => {
    const node = normalizeActionNode({
      kind: 'action',
      order: 1,
      action: 'swipe_to_find',
      element_id: 7,
      params: {
        direction: 'up',
        max_swipes: 5,
        wait_timeout: 2,
        duration: 500,
        continue_on_failure: true,
      },
      continue_on_failure: false,
    } as ActionNode)

    expect(node.params).toMatchObject({ percent: 0.2, settle_ms: 500 })
    expect('continue_on_failure' in (node.params ?? {})).toBe(false)
  })

  it('旧步骤路径回填默认值且保留显式边界值', () => {
    const step = normalizeStep({
      order: 1,
      action: 'swipe_to_find',
      element_id: 7,
      params: {
        percent: 0.05,
        settle_ms: 0,
      },
      continue_on_failure: false,
    })

    expect(step.params).toMatchObject({ percent: 0.05, settle_ms: 0 })
    expect(step.params?.duration).toBe(500)
    expect(step.params?.wait_timeout).toBe(2)
  })
})
