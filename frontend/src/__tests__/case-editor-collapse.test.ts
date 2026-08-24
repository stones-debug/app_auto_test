import { describe, expect, it } from 'vitest'

import type { Assertion, Step } from '@/api/cases'
import { buildCaseEditorSummary, caseEditorSummaryText } from '@/utils/caseEditorSummary'

describe('用例编辑器折叠摘要', () => {
  it('按阶段统计步骤，并统计断言和有效变量', () => {
    const steps = [
      { order: 1, phase: 'setup', action: 'click', continue_on_failure: false },
      { order: 1, phase: 'main', action: 'input', continue_on_failure: false },
      { order: 2, action: 'click', continue_on_failure: false },
      { order: 1, phase: 'teardown', action: 'clear', continue_on_failure: false },
    ] satisfies Step[]
    const assertions = [
      { order: 1, type: 'text_equals' },
      { order: 2, type: 'element_exists' },
    ] satisfies Assertion[]

    expect(buildCaseEditorSummary(
      steps,
      assertions,
      [{ key: 'account' }, { key: '  ' }, { key: '' }],
    )).toEqual({
      setup: 1,
      main: 2,
      teardown: 1,
      assertions: 2,
      variables: 1,
    })
  })

  it('生成单行摘要文案，并按真实执行顺序展示断言和后置步骤', () => {
    const text = caseEditorSummaryText('登录模块', {
      setup: 1,
      main: 3,
      teardown: 2,
      assertions: 4,
      variables: 1,
    })

    expect(text).toBe('模块：登录模块 · 前置 1 · 主体 3 · 断言 4 · 后置 2 · 变量 1')
    expect(text.indexOf('断言')).toBeLessThan(text.indexOf('后置'))
  })
})
