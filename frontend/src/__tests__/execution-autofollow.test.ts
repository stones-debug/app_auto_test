import { describe, expect, it } from 'vitest'

import {
  executionCaseKey,
  extractActiveExecutionTarget,
  findRunningExecutionTarget,
  isFirstPassedTransition,
  matchesExecutionPath,
} from '@/utils/executionAutofollow'

describe('执行详情自动跟随与用例收起状态', () => {
  it('从 node_started 提取执行实例 ID，并拒绝无效节点', () => {
    expect(extractActiveExecutionTarget({
      execution_node_id: '301',
      execution_case_id: '201',
      execution_suite_id: 101,
    })).toEqual({
      execution_node_id: 301,
      execution_case_id: 201,
      execution_suite_id: 101,
    })
    expect(extractActiveExecutionTarget({ execution_case_id: 201 })).toBeNull()
    expect(extractActiveExecutionTarget({ execution_node_id: 0 })).toBeNull()
    expect(extractActiveExecutionTarget({ execution_node_id: -3 })).toBeNull()
    expect(extractActiveExecutionTarget({ execution_node_id: 3.5 })).toBeNull()
    expect(extractActiveExecutionTarget({
      execution_node_id: 3,
      execution_case_id: 2.2,
      execution_suite_id: -1,
    })).toMatchObject({
      execution_node_id: 3,
      execution_case_id: null,
      execution_suite_id: null,
    })
  })

  it('从 REST 套件树恢复 running 的节点、套件步骤和 legacy 断言', () => {
    expect(findRunningExecutionTarget([{
      id: 101,
      setup_steps: [{ id: 11, status: 'passed' }],
      cases: [{
        id: 201,
        nodes: [{ id: 301, status: 'running' }],
      }],
      teardown_steps: [],
    }])).toEqual({
      execution_node_id: 301,
      execution_case_id: 201,
      execution_suite_id: 101,
    })

    expect(findRunningExecutionTarget([{
      id: 102,
      setup_steps: [{ id: 12, status: 'running' }],
      cases: [],
      teardown_steps: [],
    }])).toMatchObject({ execution_node_id: 12, execution_suite_id: 102, execution_case_id: null })

    expect(findRunningExecutionTarget([{
      id: 103,
      cases: [{
        id: 203,
        steps: [{ id: 13, status: 'passed', assertions: [{ id: 14, status: 'running' }] }],
      }],
    }])).toMatchObject({ execution_node_id: 14, execution_case_id: 203, execution_suite_id: 103 })
  })

  it('按套件/用例/节点执行实例 ID 匹配路径，不混用原资产 ID', () => {
    const target = extractActiveExecutionTarget({
      execution_node_id: 3,
      execution_case_id: 2,
      execution_suite_id: 1,
    })!
    expect(matchesExecutionPath(target, {
      execution_node_id: 3,
      execution_case_id: 2,
      execution_suite_id: 1,
    })).toBe(true)
    expect(matchesExecutionPath(target, {
      execution_node_id: 3,
      execution_case_id: 22,
      execution_suite_id: 1,
    })).toBe(false)
  })

  it('仅首次非 passed → passed 转变触发收起', () => {
    expect(isFirstPassedTransition('running', 'passed', false)).toBe(true)
    expect(isFirstPassedTransition('running', 'failed', false)).toBe(false)
    expect(isFirstPassedTransition('passed', 'passed', false)).toBe(false)
    expect(isFirstPassedTransition('failed', 'passed', true)).toBe(false)
  })

  it('用执行套件/用例 ID 生成稳定的收起状态键', () => {
    expect(executionCaseKey(10, 20)).toBe('10:20')
    expect(executionCaseKey(null, 20)).toBe('virtual:20')
  })
})
