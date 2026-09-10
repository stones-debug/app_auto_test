import { describe, expect, it } from 'vitest'

import type { TestModule } from '@/api/modules'
import { planModuleMove, planRootMove, resolveDropPosition } from '@/utils/moduleTreeDrag'

function mod(id: number, parentId: number | null, sortOrder: number, name = `M${id}`): TestModule {
  return {
    id,
    project_id: 1,
    parent_id: parentId,
    name,
    sort_order: sortOrder,
    scope: 'case',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

/**
 * 树结构：
 *   A(1, root 0) ── B(3, 子) ── D(4)
 *                            └─ E(5)
 *   C(2, root 1)
 */
const TREE = [mod(1, null, 0, 'A'), mod(2, null, 1, 'C'), mod(3, 1, 0, 'B'), mod(4, 3, 0, 'D'), mod(5, 3, 1, 'E')]

describe('resolveDropPosition', () => {
  it('上 25% 落在目标之前，下 25% 落在目标之后，中间落在目标内部', () => {
    expect(resolveDropPosition(0, 40)).toBe('before')
    expect(resolveDropPosition(9.9, 40)).toBe('before')
    expect(resolveDropPosition(10.4, 40)).toBe('inside')
    expect(resolveDropPosition(20, 40)).toBe('inside')
    expect(resolveDropPosition(30.4, 40)).toBe('after')
    expect(resolveDropPosition(40, 40)).toBe('after')
  })

  it('行高非法时按“之前”处理，不产生 NaN 边界', () => {
    expect(resolveDropPosition(0, 0)).toBe('before')
    expect(resolveDropPosition(5, -10)).toBe('after')
  })
})

describe('planModuleMove', () => {
  it('拖到目标内部时成为其子模块并追加到末尾', () => {
    expect(planModuleMove(TREE, 1, { moduleId: 2, position: 'inside' })).toEqual({
      moduleId: 1,
      parentId: 2,
      beforeId: null,
    })
  })

  it('拖到同级目标之前/之后时计算正确的 parent_id 与 before_id', () => {
    expect(planModuleMove(TREE, 5, { moduleId: 4, position: 'before' })).toEqual({
      moduleId: 5,
      parentId: 3,
      beforeId: 4,
    })
    // D 在 E 之后：已是该父级最后一个兄弟 → before_id 为 null
    expect(planModuleMove(TREE, 4, { moduleId: 5, position: 'after' })).toEqual({
      moduleId: 4,
      parentId: 3,
      beforeId: null,
    })
  })

  it('after 落在最后一位的兄弟上时升级为父级末尾', () => {
    // A 放到 C 之后：roots 去掉 A 只剩 C，C 无后继 → before_id=null（追加根层级末尾）
    expect(planModuleMove(TREE, 1, { moduleId: 2, position: 'after' })).toEqual({
      moduleId: 1,
      parentId: null,
      beforeId: null,
    })
  })

  it('阻止拖到自身', () => {
    expect(planModuleMove(TREE, 1, { moduleId: 1, position: 'inside' })).toBeNull()
    expect(planModuleMove(TREE, 1, { moduleId: 1, position: 'before' })).toBeNull()
  })

  it('阻止拖到自己的子孙里（inside 与 before/after 都算）', () => {
    // B/D/E 都在 A 的子树内
    expect(planModuleMove(TREE, 1, { moduleId: 3, position: 'inside' })).toBeNull()
    expect(planModuleMove(TREE, 1, { moduleId: 5, position: 'after' })).toBeNull()
    // 拖到自己的子模块前面，会让模块成为自己的父级
    expect(planModuleMove(TREE, 3, { moduleId: 4, position: 'before' })).toBeNull()
  })

  it('落点与当前位置一致时返回 null（不发无意义请求）', () => {
    // A 已经在 C 之前
    expect(planModuleMove(TREE, 1, { moduleId: 2, position: 'before' })).toBeNull()
  })

  it('目标或拖拽源不存在时返回 null', () => {
    expect(planModuleMove(TREE, 999, { moduleId: 1, position: 'inside' })).toBeNull()
    expect(planModuleMove(TREE, 1, { moduleId: 999, position: 'inside' })).toBeNull()
  })
})

describe('planRootMove', () => {
  it('把子模块提升为顶级模块并排在最后', () => {
    expect(planRootMove(TREE, 3)).toEqual({ moduleId: 3, parentId: null, beforeId: null })
  })

  it('把非末尾的根模块移到根层级末尾', () => {
    expect(planRootMove(TREE, 1)).toEqual({ moduleId: 1, parentId: null, beforeId: null })
  })

  it('已经在根层级末尾时返回 null', () => {
    expect(planRootMove(TREE, 2)).toBeNull()
  })

  it('模块不存在时返回 null', () => {
    expect(planRootMove(TREE, 999)).toBeNull()
  })
})
