import { beforeEach, describe, expect, it } from 'vitest'

import type { TestModule } from '@/api/modules'
import {
  buildModuleTree,
  collectSubtreeIds,
  flattenModuleTree,
  loadCollapsed,
  saveCollapsed,
  siblingsOf,
} from '@/utils/moduleTree'

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

beforeEach(() => {
  localStorage.clear()
})

describe('buildModuleTree', () => {
  it('按 parent_id 建树并按 sort_order 排序（乱序输入也要正确）', () => {
    // A(1) ── B(3) ── D(4), E(5)；C(2) 是另一个根
    const tree = buildModuleTree([
      mod(4, 3, 0, 'D'),
      mod(1, null, 0, 'A'),
      mod(3, 1, 0, 'B'),
      mod(2, null, 1, 'C'),
      mod(5, 3, 1, 'E'),
    ])
    expect(tree.map((node) => node.name)).toEqual(['A', 'C'])
    expect(tree[0].children.map((node) => node.name)).toEqual(['B'])
    expect(tree[0].children[0].children.map((node) => node.name)).toEqual(['D', 'E'])
  })

  it('父节点缺失（已删除/跨项目脏数据）时提升为根，不丢模块', () => {
    const tree = buildModuleTree([mod(1, null, 0, 'A'), mod(9, 404, 0, '孤儿')])
    expect(tree.map((node) => node.name).sort()).toEqual(['A', '孤儿'])
    expect(tree.find((node) => node.name === '孤儿')?.children).toEqual([])
  })

  it('自引用脏数据不会死循环，也不会挂到自己下面', () => {
    const tree = buildModuleTree([mod(7, 7, 0, '自环')])
    expect(tree.map((node) => node.id)).toEqual([7])
    expect(tree[0].children).toEqual([])
  })
})

describe('flattenModuleTree', () => {
  it('折叠节点的子树不进入可见行，层级用于缩进', () => {
    const tree = buildModuleTree([mod(1, null, 0, 'A'), mod(2, 1, 0, 'B'), mod(3, 2, 0, 'C')])
    expect(flattenModuleTree(tree, new Set()).map((row) => [row.node.name, row.level])).toEqual([
      ['A', 0],
      ['B', 1],
      ['C', 2],
    ])
    expect(flattenModuleTree(tree, new Set([1])).map((row) => row.node.name)).toEqual(['A'])
    expect(flattenModuleTree(tree, new Set([2])).map((row) => row.node.name)).toEqual(['A', 'B'])
  })
})

describe('collectSubtreeIds', () => {
  it('包含自身与全部子孙', () => {
    const modules = [mod(1, null, 0), mod(2, 1, 0), mod(3, 2, 0), mod(4, null, 1)]
    expect([...collectSubtreeIds(modules, 1)].sort()).toEqual([1, 2, 3])
    expect([...collectSubtreeIds(modules, 4)]).toEqual([4])
  })

  it('数据里存在环时也能终止', () => {
    const modules = [mod(1, 2, 0), mod(2, 1, 0)]
    expect([...collectSubtreeIds(modules, 1)].sort()).toEqual([1, 2])
  })
})

describe('siblingsOf', () => {
  it('只返回同父级并按 sort_order, id 排序', () => {
    const modules = [mod(1, null, 1), mod(2, null, 0), mod(3, 1, 0)]
    expect(siblingsOf(modules, null).map((module) => module.id)).toEqual([2, 1])
    expect(siblingsOf(modules, 1).map((module) => module.id)).toEqual([3])
  })
})

describe('折叠状态持久化', () => {
  it('按项目 + scope 隔离存取', () => {
    saveCollapsed(1, 'case', new Set([3, 5]))
    saveCollapsed(1, 'suite', new Set([9]))
    expect([...loadCollapsed(1, 'case')].sort()).toEqual([3, 5])
    expect([...loadCollapsed(1, 'suite')]).toEqual([9])
    expect([...loadCollapsed(2, 'case')]).toEqual([])
  })

  it('存储内容损坏时回落到全部展开', () => {
    localStorage.setItem('module-tree:1:case', '{not json')
    expect([...loadCollapsed(1, 'case')]).toEqual([])
    localStorage.setItem('module-tree:1:case', '{"a":1}')
    expect([...loadCollapsed(1, 'case')]).toEqual([])
  })
})
