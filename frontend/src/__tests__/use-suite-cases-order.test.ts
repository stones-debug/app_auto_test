import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import type { SuiteCase } from '@/api/suites'

const { reorder, list } = vi.hoisted(() => ({ reorder: vi.fn(), list: vi.fn() }))

vi.mock('@/api/suites', () => ({
  addSuiteCases: vi.fn(),
  listSuiteCases: list,
  removeSuiteCase: vi.fn(),
  reorderSuiteCases: reorder,
}))
vi.mock('@/api/cases', () => ({ listCases: vi.fn() }))

import { useSuiteCases } from '@/composables/useSuiteCases'

const item = (caseId: number): SuiteCase => ({ id: caseId, case_id: caseId, case_name: `case-${caseId}`, module_name: null, sort_order: caseId })

beforeEach(() => {
  reorder.mockReset()
  list.mockReset()
  ;(globalThis as any).ElMessage = { success: vi.fn(), error: vi.fn(), warning: vi.fn() }
})

describe('useSuiteCases 编排排序', () => {
  it('成功请求发送完整新顺序', async () => {
    const active = ref<number | null>(1)
    const state = useSuiteCases(1, active)
    list.mockResolvedValue([item(1), item(2), item(3)])
    await state.loadSuiteCases()
    reorder.mockResolvedValue(undefined)
    await expect(state.moveCaseToPosition(3, 2)).resolves.toBe(true)
    expect(reorder).toHaveBeenCalledWith(1, [1, 3, 2])
  })

  it('同位置或非法位置不请求', async () => {
    const active = ref<number | null>(1)
    const state = useSuiteCases(1, active)
    list.mockResolvedValue([item(1), item(2)])
    await state.loadSuiteCases()
    await state.moveCaseToPosition(1, 1)
    await state.moveCaseToPosition(1, 0)
    expect(reorder).not.toHaveBeenCalled()
  })

  it('失败时回滚前端顺序', async () => {
    const active = ref<number | null>(1)
    const state = useSuiteCases(1, active)
    list.mockResolvedValue([item(1), item(2), item(3)])
    await state.loadSuiteCases()
    reorder.mockRejectedValue(new Error('network'))
    await state.moveCaseToPosition(3, 1)
    expect(state.suiteCases.value.map((entry) => entry.case_id)).toEqual([1, 2, 3])
  })

  it('旧套件请求完成后不污染当前套件顺序', async () => {
    const active = ref<number | null>(1)
    const state = useSuiteCases(1, active)
    list.mockResolvedValue([item(1), item(2)])
    await state.loadSuiteCases()
    let resolveRequest!: () => void
    reorder.mockImplementation(() => new Promise<void>((resolve) => { resolveRequest = resolve }))
    const moving = state.moveCaseToPosition(2, 1)
    active.value = 2
    state.suiteCases.value = [item(9), item(8)]
    resolveRequest()
    await moving
    expect(state.suiteCases.value.map((entry) => entry.case_id)).toEqual([9, 8])
  })
})
