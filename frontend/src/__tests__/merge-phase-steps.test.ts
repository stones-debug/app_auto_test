import { describe, expect, it } from 'vitest'

import type { Step, StepPhase } from '@/api/cases'
import { mergePhaseSteps } from '@/utils/mergePhaseSteps'

function mk(phase: StepPhase, order: number): Step {
  return { phase, order, action: 'click', params: {}, continue_on_failure: false }
}

describe('mergePhaseSteps 跨相位拖拽重组', () => {
  it('把拖入的目标相位步骤合并进全量 steps，其余相位保持原顺序', () => {
    const all: Step[] = [mk('setup', 1), mk('main', 1), mk('main', 2), mk('teardown', 1)]
    const targetMain = [mk('main', 1), mk('main', 2), mk('setup', 99)] // 拖入一个 setup 步到 main
    const merged = mergePhaseSteps(all, 'main', targetMain)

    expect(merged.filter((s) => s.phase === 'main').map((s) => s.order)).toEqual([1, 2, 3])
    expect(merged.filter((s) => s.phase === 'setup').length).toBe(1)
    expect(merged.filter((s) => s.phase === 'teardown').length).toBe(1)
  })

  it('复用传入步骤对象以保持引用身份（折叠状态对应）', () => {
    const all: Step[] = [mk('setup', 1), mk('main', 1)]
    const dragged = mk('setup', 99)
    const merged = mergePhaseSteps(all, 'main', [all[1]!, dragged])
    expect(merged.includes(dragged)).toBe(true)
    expect(merged.includes(all[1])).toBe(true)
    expect(dragged.phase).toBe('main')
    expect(dragged.order).toBe(2)
  })

  it('从相位移除步骤后，其余相位顺序与编号保持回落', () => {
    const all: Step[] = [mk('main', 1), mk('main', 2), mk('teardown', 1)]
    const targetMain = [all[1]!] // 拖走 index 0 的 main 步骤
    const merged = mergePhaseSteps(all, 'main', targetMain)
    expect(merged.filter((s) => s.phase === 'main').map((s) => s.order)).toEqual([1])
  })
})
