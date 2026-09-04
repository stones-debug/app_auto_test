import { describe, expect, it } from 'vitest'

import { digitsOnly, moveToPosition } from '@/utils/suiteCaseOrder'

describe('套件用例编号排序', () => {
  it('只保留数字字符', () => expect(digitsOnly('1a-2.3')).toBe('123'))

  it('最后一项可移动到第二位', () => expect(moveToPosition(['a', 'b', 'c', 'd'], 3, 2)).toEqual(['a', 'd', 'b', 'c']))

  it('向后移动时其它项自动顺移', () => expect(moveToPosition(['a', 'b', 'c', 'd'], 0, 3)).toEqual(['b', 'c', 'a', 'd']))

  it('同位置保持原顺序', () => expect(moveToPosition(['a', 'b'], 1, 2)).toEqual(['a', 'b']))

  it('非法位置不产生新顺序', () => {
    expect(moveToPosition(['a', 'b'], 0, 0)).toBeNull()
    expect(moveToPosition(['a', 'b'], 0, 3)).toBeNull()
  })
})
