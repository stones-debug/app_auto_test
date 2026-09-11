import { describe, expect, it, vi } from 'vitest'

import {
  buildOccurrenceUpdates,
  buildVariableUpdates,
  handleVariableAreaClick,
  membershipVariablesToPreview,
  variableDisplayText,
  variableEditSeed,
  variableOverrideState,
  variablePreviewChips,
  variableQuickUpdates,
  variableRestoreUpdates,
  variableStatusMeta,
  variableToken,
  type EditorVariable,
  type ProfileCaseVariableLike,
} from '@/utils/caseVariables'

function profileVariable(
  overrides: Partial<ProfileCaseVariableLike> = {},
): ProfileCaseVariableLike {
  return {
    name: 'port_name',
    status: 'inherited',
    reference_count: 2,
    inherited_value: 'COM1',
    inherited_scope: 'project',
    references: [
      { node_key: 'k1', node_type: 'step', override_enabled: false, override_value: '' },
      { node_key: 'k2', node_type: 'assertion', override_enabled: false, override_value: '' },
    ],
    ...overrides,
  }
}

describe('用例变量摘要（套件编排项行）', () => {
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

describe('覆盖增量提交（套件编排项）', () => {
  const variables: EditorVariable[] = [
    { name: 'port_name', status: 'inherited', reference_count: 1, inherited_value: 'COM1', inherited_scope: '项目', override_enabled: false, override_value: '' },
    { name: 'baud_rate', status: 'overridden', reference_count: 1, inherited_value: '9600', inherited_scope: '项目', override_enabled: true, override_value: '115200' },
  ]

  it('只提交变化项，关闭开关提交 null 恢复继承', () => {
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
})

describe('APP 档案用例行变量就地覆盖', () => {
  it('未覆盖时沿用继承值，各节点取值一致即视为统一', () => {
    const state = variableOverrideState(profileVariable())
    expect(state).toEqual({ overridden: false, uniform: true, value: 'COM1' })
    expect(variableDisplayText(profileVariable())).toBe('COM1')
    expect(variableEditSeed(profileVariable())).toBe('COM1')
  })

  it('各节点覆盖值不一致时展示「多个值」，编辑初始值留空等待统一', () => {
    const variable = profileVariable({
      status: 'mixed',
      references: [
        { node_key: 'k1', node_type: 'step', override_enabled: true, override_value: 'A' },
        { node_key: 'k2', node_type: 'assertion', override_enabled: true, override_value: 'B' },
      ],
    })
    const state = variableOverrideState(variable)
    expect(state.overridden).toBe(true)
    expect(state.uniform).toBe(false)
    expect(variableDisplayText(variable)).toBe('多个值')
    expect(variableEditSeed(variable)).toBe('')
  })

  it('空值与未定义区分展示', () => {
    const empty = profileVariable({
      status: 'overridden',
      references: [
        { node_key: 'k1', node_type: 'step', override_enabled: true, override_value: '' },
        { node_key: 'k2', node_type: 'assertion', override_enabled: true, override_value: '' },
      ],
    })
    expect(variableDisplayText(empty)).toBe('（空）')

    const undefinedVariable = profileVariable({
      status: 'undefined',
      inherited_value: null,
      inherited_scope: null,
    })
    expect(variableDisplayText(undefinedVariable)).toBe('未定义')
  })

  it('一个值写入该变量的全部引用节点；null 表示各节点恢复原值', () => {
    const variable = profileVariable()
    expect(buildVariableUpdates(variable, 'COM9')).toEqual([
      { node_type: 'step', node_key: 'k1', name: 'port_name', value: 'COM9' },
      { node_type: 'assertion', node_key: 'k2', name: 'port_name', value: 'COM9' },
    ])
    expect(variableRestoreUpdates(variable)).toEqual([
      { node_type: 'step', node_key: 'k1', name: 'port_name', value: null },
      { node_type: 'assertion', node_key: 'k2', name: 'port_name', value: null },
    ])
  })

  it('就地提交：值未变化时不产生更新，避免空提交推进 revision', () => {
    expect(variableQuickUpdates(profileVariable(), 'COM1')).toBeNull()
    expect(
      variableQuickUpdates(
        profileVariable({
          status: 'overridden',
          references: [
            { node_key: 'k1', node_type: 'step', override_enabled: true, override_value: 'COM2' },
            { node_key: 'k2', node_type: 'assertion', override_enabled: true, override_value: 'COM2' },
          ],
        }),
        'COM2',
      ),
    ).toBeNull()
    // 未定义变量留空同样没有变更
    expect(
      variableQuickUpdates(
        profileVariable({ status: 'undefined', inherited_value: null, inherited_scope: null }),
        '',
      ),
    ).toBeNull()

    expect(variableQuickUpdates(profileVariable(), 'COM3')).toEqual([
      { node_type: 'step', node_key: 'k1', name: 'port_name', value: 'COM3' },
      { node_type: 'assertion', node_key: 'k2', name: 'port_name', value: 'COM3' },
    ])
  })

  it('多值状态：即使统一为空串也要提交（属于真实变更）', () => {
    const mixed = profileVariable({
      status: 'mixed',
      references: [
        { node_key: 'k1', node_type: 'step', override_enabled: true, override_value: 'A' },
        { node_key: 'k2', node_type: 'assertion', override_enabled: false, override_value: '' },
      ],
    })
    expect(variableQuickUpdates(mixed, '')).toEqual([
      { node_type: 'step', node_key: 'k1', name: 'port_name', value: '' },
      { node_type: 'assertion', node_key: 'k2', name: 'port_name', value: '' },
    ])
  })
})
