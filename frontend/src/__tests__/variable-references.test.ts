import { describe, expect, it } from 'vitest'

import { extractVariableReferences, nodeVariableReferences, stepVariableReferences } from '@/utils/variableReferences'

describe('APP 档案步骤变量引用', () => {
  it('递归提取嵌套参数中的变量并保持首次顺序、去重', () => {
    expect(extractVariableReferences({
      first: '${one}/${two}',
      nested: ['${one}', { value: 'prefix-${three}' }],
    })).toEqual(['one', 'two', 'three'])
  })

  it('只扫描动作步骤参数，不扫描断言或智能定位配置', () => {
    expect(stepVariableReferences({
      node_type: 'assertion',
      params: { value: '${ignored}' },
    })).toEqual([])
    expect(stepVariableReferences({
      node_type: 'step',
      params: { value: '${valid}' },
      element_id: '${not_a_param}',
    })).toEqual(['valid'])
  })

  it('兼容 parameters 旧字段', () => {
    expect(stepVariableReferences({
      node_type: 'suite_step',
      parameters: { package: '${pkg}' },
    })).toEqual(['pkg'])
  })

  it('识别中文和混合 Unicode 变量名，但拒绝点号、连字符和空格', () => {
    expect(extractVariableReferences({
      chinese: '${用户名}',
      mixed: '${用户Name_2}/${变量_3}',
      invalid: '${with-dash} ${with.dot} ${带 空格}',
    })).toEqual(['用户名', '用户Name_2', '变量_3'])
  })

  it('统一口径同时覆盖动作与断言参数，且不扫描非参数字段', () => {
    expect(nodeVariableReferences({
      node_type: 'assertion',
      params: { expected: '${expected_port}' },
    })).toEqual(['expected_port'])
    expect(nodeVariableReferences({
      node_type: 'step',
      params: { value: '${port_name}' },
      element_id: '${not_a_param}',
    })).toEqual(['port_name'])
    expect(nodeVariableReferences({
      node_type: 'case',
      params: { value: '${ignored}' },
    })).toEqual([])
  })
})
