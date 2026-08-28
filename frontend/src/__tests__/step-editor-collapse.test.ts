import { describe, expect, it } from 'vitest'

import type { Step } from '@/api/cases'
import { stepActionLabel, stepSummaryText } from '@/utils/stepEditorSummary'
describe('步骤编辑器折叠摘要', () => {
  it('显示动作、元素、关键参数和失败后继续', () => {
    const step: Step = {
      order: 1,
      phase: 'main',
      action: 'click',
      element_id: 42,
      params: { wait_timeout: 5 },
      description: '点击登录按钮',
      continue_on_failure: true,
    }

    expect(stepActionLabel(step)).toBe('点击元素')
    expect(stepSummaryText(step)).toBe(
      '失败后继续 · 元素 #42 · 等待秒数：5 · 点击登录按钮',
    )
  })

  it('未选择元素时给出明确提示，空参数不占摘要空间', () => {
    const step: Step = {
      order: 1,
      phase: 'setup',
      action: 'input',
      element_id: null,
      params: { value: '', clear_first: false },
      continue_on_failure: false,
    }

    const summary = stepSummaryText(step)
    expect(summary).toContain('元素：未选择')
    expect(summary).toContain('先清空：否')
    expect(summary).not.toContain('文本：')
  })

  it('无元素且无参数的动作显示默认摘要', () => {
    const step: Step = {
      order: 1,
      action: 'back',
      params: {},
      continue_on_failure: false,
    }

    expect(stepSummaryText(step)).toBe('无附加参数')
  })

  it('收起摘要显示元素名而非编号（缺失元素则回退编号）', () => {
    const step: Step = {
      order: 1,
      phase: 'main',
      action: 'click',
      element_id: 42,
      params: {},
      continue_on_failure: false,
    }
    expect(stepSummaryText(step, new Map([[42, '登录按钮']]))).toBe('元素：登录按钮')
    // 无映射/映射不含该 id → 回退编号
    expect(stepSummaryText(step)).toBe('元素 #42')
    expect(stepSummaryText(step, new Map([[99, '其他']]))).toBe('元素 #42')
  })

  it('列表内滑动查找文字并点击 使用特化摘要', () => {
    const step: Step = {
      order: 1,
      phase: 'main',
      action: 'swipe_in_element_find_text_click',
      element_id: 7,
      params: {
        target_text: '2026',
        match_mode: 'equals',
        preferred_direction: 'up',
        max_swipes_per_direction: 8,
      },
      continue_on_failure: false,
    }
    expect(stepSummaryText(step, new Map([[7, '日期列表']]))).toBe(
      '列表：日期列表 · 文字等于“2026” · 自动上→下 · 每方向最多8次',
    )
    expect(stepActionLabel(step)).toBe('列表内滑动查找文字并点击')
  })

  it('列表内滑动查找文字并点击 未选列表给出明确提示', () => {
    const step: Step = {
      order: 1,
      phase: 'main',
      action: 'swipe_in_element_find_text_click',
      element_id: null,
      params: { target_text: '系统时间', match_mode: 'contains' },
      continue_on_failure: false,
    }
    expect(stepSummaryText(step)).toContain('列表：未选择')
    expect(stepSummaryText(step)).toContain('文字包含“系统时间”')
  })
})
