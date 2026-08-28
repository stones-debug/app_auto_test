import { describe, expect, it } from 'vitest'

import { getGroupSelectionState, setGroupSelection } from '@/utils/suiteCaseSelection'

const moduleCases = [{ id: 11 }, { id: 12 }, { id: 13 }]

describe('套件添加用例的模块全选', () => {
  it('可一键选中模块内全部用例，同时保留其它模块的已选项', () => {
    const selected = setGroupSelection(new Set([99]), moduleCases, true)

    expect([...selected].sort((a, b) => a - b)).toEqual([11, 12, 13, 99])
    expect(getGroupSelectionState(selected, moduleCases)).toEqual({ checked: true, indeterminate: false })
  })

  it('模块只选中部分用例时显示半选状态', () => {
    expect(getGroupSelectionState(new Set([11, 99]), moduleCases)).toEqual({
      checked: false,
      indeterminate: true,
    })
  })

  it('取消模块全选时不影响其它模块的选择', () => {
    const selected = setGroupSelection(new Set([11, 12, 13, 99]), moduleCases, false)

    expect([...selected]).toEqual([99])
    expect(getGroupSelectionState(selected, moduleCases)).toEqual({ checked: false, indeterminate: false })
  })
})
