import { describe, expect, it } from 'vitest'

import { buildProfileSkipTarget } from '@/utils/appProfileSkip'

describe('APP 档案跳过目标', () => {
  it('共享用例目标携带当前套件上下文', () => {
    expect(buildProfileSkipTarget({
      node_type: 'case',
      id: 22,
      _suiteId: 11,
      _caseId: 22,
    })).toEqual({ type: 'case', suite_id: 11, case_id: 22 })
  })

  it('步骤和断言目标携带当前套件上下文', () => {
    expect(buildProfileSkipTarget({
      node_type: 'step',
      node_key: 'node-key',
      _suiteId: 11,
      _caseId: 22,
    })).toEqual({
      type: 'step',
      suite_id: 11,
      case_id: 22,
      node_key: 'node-key',
    })
  })

  it('套件前后置步骤目标仅需套件与节点 key', () => {
    expect(buildProfileSkipTarget({
      node_type: 'suite_step',
      node_key: 'suite-node-key',
      _suiteId: 11,
    })).toEqual({
      type: 'suite_step',
      suite_id: 11,
      node_key: 'suite-node-key',
    })
  })

  it('缺少 node_key 时拒绝生成套件步骤目标', () => {
    expect(buildProfileSkipTarget({ node_type: 'suite_step', _suiteId: 11 })).toBeNull()
  })

  it('缺少套件上下文时拒绝生成用例目标', () => {
    expect(buildProfileSkipTarget({ node_type: 'case', id: 22 })).toBeNull()
  })
})
