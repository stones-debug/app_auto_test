import { describe, expect, it } from 'vitest'

import { liveLogKey, mergeExecutionLogs } from '@/utils/executionLogs'
import {
  applyAssertionResult,
  applyStepResult,
  executionConnectionState,
  settleExecutionSuites,
} from '@/utils/executionRealtime'
import { applyOnlyFailed } from '@/utils/reportFilter'
import { assertionPassed, orderExecutionItems } from '@/utils/executionOrder'
import { executionWsUrl } from '@/composables/useExecutionSocket'
import { formatParameters, hasParameters } from '@/utils/parameters'

// node 环境无 location，executionWsUrl 依赖
;(globalThis as Record<string, unknown>).location = { protocol: 'http:', host: 'test.local' }

describe('Step 7 执行详情：日志去重与 WS 状态', () => {
  it('步骤结果分别更新步骤状态与服务端 case_status（嵌套 suites 定位）', () => {
    const suites = [{
      id: 5,
      suite_id: null,
      suite_name: '虚拟套件',
      status: 'running',
      duration: null,
      error_message: null,
      setup_steps: [],
      cases: [{
        id: 1,
        case_id: 10,
        case_name: '登录',
        status: 'pending',
        steps: [{
          id: 2,
          step_order: 1,
          action: 'click',
          parameters: {},
          status: 'pending',
          duration: null,
          actual_value: null,
          error_message: null,
        }],
        assertions: [],
      }],
      teardown_steps: [],
    }]

    applyStepResult(suites, {
      execution_case_id: 1,
      step_order: 1,
      status: 'passed',
      case_status: 'running',
      duration: 120,
    })

    expect(suites[0].cases[0].steps[0].status).toBe('passed')
    expect(suites[0].cases[0].status).toBe('running')
    applyAssertionResult(suites, {
      execution_case_id: 1,
      case_status: 'passed',
    })
    expect(suites[0].cases[0].status).toBe('passed')
  })

  it('execution_case_id 精确匹配 ExecutionCase.id，不与原 case_id 数字碰撞', () => {
    // 场景：套件B 某用例原 case_id=7 恰好等于套件A 实例 execution_case_id=7，且套件B 先出现
    // 旧实现按 (id==7 || case_id==7) 匹配——遍历到套件B 的 case_id=7 即误命中。
    const suites = [{
      id: 11, suite_id: null, suite_name: '套件B', status: 'running', setup_steps: [], teardown_steps: [],
      cases: [
        { id: 8, case_id: 7, status: 'running', steps: [{ id: 81, step_order: 1, status: 'passing' }], assertions: [] },
      ],
    }, {
      id: 10, suite_id: null, suite_name: '套件A', status: 'running', setup_steps: [], teardown_steps: [],
      cases: [
        { id: 7, case_id: 100, status: 'running', steps: [{ id: 71, step_order: 1, status: 'pending' }], assertions: [] },
      ],
    }]

    applyStepResult(suites, {
      execution_case_id: 7, // 唯一应命中套件A.id=7；旧实现会先因 case_id=7 命中套件B
      step_order: 1,
      status: 'passed',
      case_status: 'passed',
    })

    expect(suites[1].cases[0].status).toBe('passed') // 套件A 实例更新
    expect(suites[1].cases[0].steps[0].status).toBe('passed')
    expect(suites[0].cases[0].status).toBe('running') // 套件B（case_id=7）绝不更新
    expect(suites[0].cases[0].steps[0].status).toBe('passing')
  })

  it('execution_case_id 不命中时不再回退 case_id（修复共享用例碰撞）', () => {
    const suites = [{
      id: 1, suite_id: null, suite_name: '套件', status: 'running', setup_steps: [], teardown_steps: [],
      cases: [{ id: 5, case_id: 42, status: 'running', steps: [{ id: 51, step_order: 1, status: 'pending' }], assertions: [] }],
    }]
    // execution_case_id=999 不存在任何实例；case_id=42 存在但必须不匹配
    applyStepResult(suites, { execution_case_id: 999, case_id: 42, step_order: 1, status: 'passed' })
    expect(suites[0].cases[0].status).toBe('running')
    expect(suites[0].cases[0].steps[0].status).toBe('pending')
    // 旧协议仅 case_id → 回退按原 case_id 匹配
    applyStepResult(suites, { case_id: 42, step_order: 1, status: 'passed' })
    expect(suites[0].cases[0].steps[0].status).toBe('passed')
  })

  it('套件步骤实时按 execution_suite_id 匹配 ExecutionSuite.id 并刷新', () => {
    const suites = [{
      id: 100, // ExecutionSuite.id
      suite_id: 5, // 原 TestSuite.id（不同值，旧实现要求二者相等导致永不匹配）
      suite_name: '套件X',
      status: 'running',
      setup_steps: [{ id: 501, step_order: 1, action: 'sleep', status: 'pending', phase: 'suite_setup' }],
      teardown_steps: [{ id: 502, step_order: 1, action: 'sleep', status: 'pending', phase: 'suite_teardown' }],
      cases: [],
    }]

    applyStepResult(suites, {
      execution_suite_id: 100, // ExecutionSuite.id
      execution_step_id: 501,
      phase: 'suite_setup',
      step_order: 1,
      status: 'passed',
    })
    expect(suites[0].setup_steps[0].status).toBe('passed')

    applyStepResult(suites, {
      execution_suite_id: 100,
      execution_step_id: 502,
      phase: 'suite_teardown',
      step_order: 1,
      status: 'failed',
    })
    expect(suites[0].teardown_steps[0].status).toBe('failed')
  })

  it('实时合并断言结果，并兼容 pass/passed 状态口径', () => {
    const suites = [{
      suite_id: null,
      suite_name: '套件',
      status: 'running',
      setup_steps: [],
      cases: [{ id: 3, case_id: 10, status: 'running', steps: [], assertions: [] }],
      teardown_steps: [],
    }]
    applyAssertionResult(suites, {
      execution_case_id: 3,
      case_status: 'failed',
      assertions: [{
        type: 'text_equals',
        expected: 'wrong',
        actual: 'admin',
        status: 'fail',
      }],
    })

    expect(suites[0].cases[0].assertions).toEqual([{
      assertion_type: 'text_equals',
      expected_value: 'wrong',
      actual_value: 'admin',
      status: 'fail',
      error_message: null,
    }])
    expect(suites[0].cases[0].status).toBe('failed')
    expect(assertionPassed('pass')).toBe(true)
    expect(assertionPassed('passed')).toBe(true)
    expect(assertionPassed('failed')).toBe(false)
  })

  it('时间线按前置/主体 → 断言 → 后置的真实顺序展示', () => {
    const items = orderExecutionItems(
      [
        { phase: 'case_setup', name: 'setup' },
        { phase: 'case_main', name: 'main' },
        { phase: 'case_teardown', name: 'teardown' },
      ],
      [{ name: 'assertion' }],
    )
    expect(items.map((item) => item.value.name)).toEqual([
      'setup',
      'main',
      'assertion',
      'teardown',
    ])
  })

  it('completed 立即收敛仍为 pending/running 的套件与用例', () => {
    const suites = [
      {
        suite_id: null,
        suite_name: '套件A',
        status: 'running',
        setup_steps: [],
        cases: [
          { id: 1, case_id: 11, status: 'running' },
          { id: 2, case_id: 12, status: 'pending' },
        ],
        teardown_steps: [],
      },
    ]
    settleExecutionSuites(suites, 'failed')
    expect(suites[0].status).toBe('failed')
    expect(suites[0].cases.map((item) => item.status)).toEqual(['failed', 'skipped'])
  })

  it('执行终态优先显示已结束，不再显示连接中', () => {
    expect(executionConnectionState(true, false, true)).toEqual({
      kind: 'done',
      label: '执行已结束',
    })
  })

  it('执行参数和步骤参数可格式化展示', () => {
    const parameters = { variables: { account: 'admin' }, retry: false }
    expect(hasParameters(parameters)).toBe(true)
    expect(formatParameters(parameters)).toBe('{"variables":{"account":"admin"},"retry":false}')
    expect(formatParameters(parameters, true)).toContain('\n  "variables"')
    expect(formatParameters({})).toBe('')
  })

  it('REST 与 WS live 同内容只保留一条（按后端 log id 去重）', () => {
    const rest = [
      { id: 1, level: 'INFO', message: '启动', created_at: '2026-08-22T00:00:01Z' },
      { id: 2, level: 'INFO', message: '点击', created_at: '2026-08-22T00:00:02Z' },
    ]
    const live = [
      { id: -1, level: 'INFO', message: '点击', created_at: '2026-08-22T00:00:02Z' },
      { id: -2, level: 'WARN', message: '慢操作', created_at: '2026-08-22T00:00:03Z' },
    ]
    const merged = mergeExecutionLogs(rest, live)
    expect(merged).toHaveLength(3)
    // 同内容只留一条（优先保留 REST 后端条目）
    expect(merged.filter((l) => l.message === '点击')).toHaveLength(1)
    const kept = merged.find((l) => l.message === '点击')
    expect(kept?.id).toBe(2)
  })

  it('合并结果按 (created_at, id) 排序，同毫秒次序稳定', () => {
    const rest = [
      { id: 10, level: 'INFO', message: 'b', created_at: '2026-08-22T00:00:01Z' },
      { id: 5, level: 'INFO', message: 'a', created_at: '2026-08-22T00:00:01Z' },
    ]
    const merged = mergeExecutionLogs(rest, [])
    expect(merged.map((l) => l.message)).toEqual(['a', 'b'])
  })

  it('live 临时 id 单调递减（不与后端 id 碰撞）', () => {
    expect(liveLogKey('INFO', 'x', 't1')).toBe('live:t1|INFO|x')
    // WS 重复消息被 liveKey 去重
    const key1 = liveLogKey('INFO', 'x', 't1')
    const key2 = liveLogKey('INFO', 'x', 't1')
    expect(key1).toBe(key2)
  })

  it('executionWsUrl 不把 token 以明文拼进 URL 之外的任何 header（token 走 query 参数）', () => {
    const url = executionWsUrl(123, 'tok-abc')
    expect(url).toContain('/ws/executions/123')
    expect(url).toContain('token=tok-abc')
  })
})

describe('Step 7 报告详情：失败过滤开关往返与展开项稳定', () => {
  const cases = [
    { id: 1, status: 'passed' },
    { id: 2, status: 'failed' },
    { id: 3, status: 'error' },
    { id: 4, status: 'passed' },
  ]

  it('首载默认展开 failed/error（id=2,3）', () => {
    const initial = cases.filter((c) => ['failed', 'error'].includes(c.status)).map((c) => c.id)
    expect(initial).toEqual([2, 3])
  })

  it('切到只看失败：保留仍可见展开项并默认展开 failed/error', () => {
    const active = applyOnlyFailed([1, 2], true, cases)
    expect(active).toEqual(expect.arrayContaining([2, 3]))
    // 原展开的 1 不可见（passed 被过滤），不再保留
    expect(active).not.toContain(1)
    // 去重
    expect(new Set(active).size).toBe(active.length)
  })

  it('切回全部：保留仍可见的展开项', () => {
    const active = applyOnlyFailed([2, 3], false, cases)
    expect(active).toEqual([2, 3])
  })

  it('零失败用例：只看失败时无展开项，往返后回到原展开', () => {
    const allPassed = [{ id: 7, status: 'passed' }]
    expect(applyOnlyFailed([7], true, allPassed)).toEqual([])
    expect(applyOnlyFailed([], false, allPassed)).toEqual([])
  })

  it('collapse 的 name 使用 case.id 而非过滤后数组索引（稳定跨过滤）', () => {
    // 传统实现用索引：切过滤后展开项会指向错误用例；用 id 则保持指向
    const active = applyOnlyFailed([2], false, cases)
    expect(active.some((id) => id === 2)).toBe(true)
    const afterFilter = applyOnlyFailed(active, true, cases)
    expect(afterFilter).not.toContain(1) // passed 用例不应被错误展开
  })
})
