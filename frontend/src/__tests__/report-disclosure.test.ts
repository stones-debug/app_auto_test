import { describe, expect, it } from 'vitest'

import type { ReportCase, ReportSuite } from '@/api/reports'
import caseCardSource from '@/components/ReportCaseCard.vue?raw'
import reportDetailSource from '@/views/ReportDetail.vue?raw'
import {
  emptyDisclosureState,
  initialDisclosureState,
  prepareReportCase,
  toggleDisclosureId,
  visibleDisclosureState,
} from '@/utils/reportDisclosure'

function reportCase(id: number, status = 'passed'): ReportCase {
  return {
    id,
    case_id: id + 100,
    case_name: `用例 ${id}`,
    module_name: null,
    status,
    duration: 10,
    error_message: null,
    elements: {},
    steps: [],
    assertions: [],
  }
}

function reportSuite(id: number, cases: ReportCase[]): ReportSuite {
  return {
    id,
    suite_id: id + 100,
    suite_name: `套件 ${id}`,
    suite_order: id,
    status: 'passed',
    duration: 10,
    error_message: null,
    setup_steps: [],
    cases,
    teardown_steps: [],
  }
}

describe('报告详情轻量级折叠', () => {
  it('首载只展开包含失败/异常用例的套件及对应用例', () => {
    const passedSuite = reportSuite(1, [reportCase(11)])
    const failedSuite = reportSuite(2, [reportCase(21), reportCase(22, 'failed'), reportCase(23, 'error')])

    const state = initialDisclosureState([passedSuite, failedSuite], [])

    expect([...state.expandedSuites]).toEqual([2])
    expect([...state.expandedCases]).toEqual([22, 23])
  })

  it('套件和用例状态相互独立，收起套件不会清除内部用例状态', () => {
    const state = initialDisclosureState(
      [reportSuite(1, [reportCase(11, 'failed')])],
      [],
    )

    const collapsedSuites = toggleDisclosureId(state.expandedSuites, 1)

    expect(collapsedSuites.has(1)).toBe(false)
    expect(state.expandedCases.has(11)).toBe(true)
    expect(state.expandedSuites.has(1)).toBe(true)
  })

  it('全部折叠可通过两个空集合一次完成', () => {
    const { expandedSuites, expandedCases } = emptyDisclosureState()

    expect(expandedSuites.size).toBe(0)
    expect(expandedCases.size).toBe(0)
  })

  it('只看失败时展开全部可见套件，但用例仍只自动展开失败/异常项', () => {
    const visibleSuites = [
      reportSuite(2, [reportCase(22, 'failed'), reportCase(23, 'error')]),
    ]

    const state = visibleDisclosureState(visibleSuites, [])

    expect([...state.expandedSuites]).toEqual([2])
    expect([...state.expandedCases]).toEqual([22, 23])
  })

  it('无套件的单用例报告沿用失败用例默认展开口径', () => {
    const cases = [reportCase(31), reportCase(32, 'failed')]

    const initial = initialDisclosureState([], cases)
    const visible = visibleDisclosureState([], cases)

    expect(initial.expandedSuites.size).toBe(0)
    expect([...initial.expandedCases]).toEqual([32])
    expect([...visible.expandedCases]).toEqual([32])
  })

  it('预处理一次生成断言前后步骤，保留原步骤顺序', () => {
    const source = reportCase(41)
    source.steps = [
      { id: 1, step_order: 1, action: 'click', phase: 'case_setup', parameters: {}, status: 'passed', duration: 1, actual_value: null, error_message: null, screenshot: null },
      { id: 2, step_order: 2, action: 'input', phase: 'case_main', parameters: {}, status: 'passed', duration: 1, actual_value: null, error_message: null, screenshot: null },
      { id: 3, step_order: 3, action: 'close_app', phase: 'case_teardown', parameters: {}, status: 'passed', duration: 1, actual_value: null, error_message: null, screenshot: null },
    ]

    const prepared = prepareReportCase(source)

    expect(prepared.beforeAssertionSteps.map((step) => step.id)).toEqual([1, 2])
    expect(prepared.afterAssertionSteps.map((step) => step.id)).toEqual([3])
  })

  it('收起用例时不创建步骤、断言和截图内容，报告页不再使用用例 el-collapse', () => {
    expect(caseCardSource).toContain('<div v-if="expanded" class="case-body">')
    expect(caseCardSource.indexOf('<div v-if="expanded" class="case-body">')).toBeLessThan(
      caseCardSource.indexOf('<ReportStepTable'),
    )
    expect(reportDetailSource).not.toContain('v-model="activeSuites"')
    expect(reportDetailSource).toContain('<ReportCaseCard')
  })
})
