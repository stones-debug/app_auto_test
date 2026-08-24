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
})
