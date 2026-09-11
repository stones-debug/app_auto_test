import { describe, expect, it, vi } from 'vitest'

import {
  applySameValue,
  buildOccurrenceUpdates,
  diffNodeEntries,
  handleVariableAreaClick,
  membershipVariablesToPreview,
  profileVariablesToPreview,
  variablePreviewChips,
  variableStatusMeta,
  variableToken,
  type EditorReference,
  type EditorVariable,
} from '@/utils/caseVariables'

function reference(overrides: Partial<EditorReference> = {}): EditorReference {
  return {
    node_type: 'step',
    node_key: 'k1',
    order: 1,
    node_name: '输入串口名称',
    inherited_value: 'COM1',
    override_enabled: false,
    override_value: '',
    ...overrides,
  }
}

describe('用例变量摘要', () => {
  it('行内最多展示 2 项，其余折叠为 +N 更多', () => {
    const preview = [
      { name: 'a', display_value: '1', status: 'inherited' },
      { name: 'b', display_value: '2', status: 'inherited' },
      { name: 'c', display_value: '3', status: 'inherited' },
      { name: 'd', display_value: '4', status: 'inherited' },
    ]
    const { visible, hidden } = variablePreviewChips(preview, 6)
    expect(visible.map((item) => item.name)).toEqual(['a', 'b'])
    expect(hidden).toBe(4)
  })

  it('不足 2 项时不显示更多', () => {
    const { visible, hidden } = variablePreviewChips(
      [{ name: 'a', display_value: '1', status: 'undefined' }],
      1,
    )
    expect(visible).toHaveLength(1)
    expect(hidden).toBe(0)
  })

  it('多值状态映射为「多个值」，未定义/随机有独立状态色', () => {
    expect(variableStatusMeta('mixed').label).toBe('多个值')
    expect(variableStatusMeta('overridden').tone).toBe('overridden')
    expect(variableStatusMeta('undefined').tone).toBe('undefined')
    expect(variableStatusMeta('random').tone).toBe('random')
    expect(variableToken('port_name')).toBe('${port_name}')
  })

  it('点击变量区域阻断父级事件；只读时不打开面板', () => {
    const stopPropagation = vi.fn()
    const open = vi.fn()
    handleVariableAreaClick({ stopPropagation }, false, open)
    expect(stopPropagation).toHaveBeenCalledTimes(1)
    expect(open).toHaveBeenCalledTimes(1)

    const blocked = vi.fn()
    handleVariableAreaClick({ stopPropagation: blocked }, true, open)
    expect(blocked).toHaveBeenCalledTimes(1)
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('APP 档案详情转摘要时同名变量不同节点覆盖显示「多个值」', () => {
    const preview = profileVariablesToPreview([
      {
        name: 'port_name',
        status: 'mixed',
        reference_count: 2,
        inherited_value: 'COM1',
        inherited_scope: 'project',
        references: [
          { node_key: 'k1', override_enabled: true, override_value: 'A' },
          { node_key: 'k2', override_enabled: true, override_value: 'B' },
        ],
      },
    ])
    expect(preview[0].display_value).toBe('多个值')
    expect(preview[0].status).toBe('mixed')
  })

  it('套件编排项详情转摘要保留覆盖来源', () => {
    const preview = membershipVariablesToPreview([
      {
        name: 'port_name',
        display_value: 'COM2',
        status: 'overridden',
        reference_count: 2,
        inherited_scope: null,
        override_enabled: true,
      },
    ])
    expect(preview[0]).toEqual({
      name: 'port_name',
      display_value: 'COM2',
      source: 'occurrence',
      status: 'overridden',
      reference_count: 2,
    })
  })
})

describe('覆盖增量提交', () => {
  const variables: EditorVariable[] = [
    { name: 'port_name', status: 'inherited', reference_count: 1, inherited_value: 'COM1', inherited_scope: '项目', override_enabled: false, override_value: '' },
    { name: 'baud_rate', status: 'overridden', reference_count: 1, inherited_value: '9600', inherited_scope: '项目', override_enabled: true, override_value: '115200' },
  ]

  it('编排项增量：只提交变化项，关闭开关提交 null 恢复继承', () => {
    expect(
      buildOccurrenceUpdates(variables, {
        port_name: { enabled: true, value: 'COM2' },
        baud_rate: { enabled: true, value: '115200' },
      }),
    ).toEqual({ port_name: 'COM2' })

    expect(
      buildOccurrenceUpdates(variables, {
        port_name: { enabled: false, value: '' },
        baud_rate: { enabled: false, value: '' },
      }),
    ).toEqual({ baud_rate: null })

    // 空串是合法覆盖值
    expect(
      buildOccurrenceUpdates(variables, {
        port_name: { enabled: true, value: '' },
        baud_rate: { enabled: true, value: '115200' },
      }),
    ).toEqual({ port_name: '' })
  })

  it('节点级增量：只提交变化项，禁用已覆盖节点提交 null', () => {
    const original = [reference({ node_key: 'k1' }), reference({ node_key: 'k2', override_enabled: true, override_value: 'B' })]
    const updates = diffNodeEntries(original, [
      { node_key: 'k1', node_type: 'step', name: 'port_name', enabled: true, value: 'A' },
      { node_key: 'k2', node_type: 'step', name: 'port_name', enabled: false, value: 'B' },
    ])
    expect(updates).toEqual([
      { node_type: 'step', node_key: 'k1', name: 'port_name', value: 'A' },
      { node_type: 'step', node_key: 'k2', name: 'port_name', value: null },
    ])
  })

  it('「全部设为同一值」仍是多个节点级覆盖', () => {
    const entries = applySameValue([reference({ node_key: 'k1' }), reference({ node_key: 'k2' })], 'port_name', 'SAME')
    expect(entries).toEqual([
      { node_key: 'k1', node_type: 'step', name: 'port_name', enabled: true, value: 'SAME' },
      { node_key: 'k2', node_type: 'step', name: 'port_name', enabled: true, value: 'SAME' },
    ])
  })
})
