import { describe, expect, it } from 'vitest'

import type { ActionNode, StepPhase } from '@/api/cases'
import { mergeFlowNodes, normalizeFlowNodeOrders } from '@/utils/flowNodeOrder'

function node(key: string, phase: StepPhase, order: number): ActionNode {
  return {
    kind: 'action',
    key,
    phase,
    order,
    action: 'click',
    params: {},
    continue_on_failure: false,
  }
}

describe('flow node phase ordering', () => {
  it('only renumbers the edited phase and stores phases by priority', () => {
    const setup = node('setup-1', 'setup', 1)
    const main1 = node('main-1', 'main', 1)
    const main2 = node('main-2', 'main', 2)
    const teardown = node('teardown-1', 'teardown', 1)

    const merged = mergeFlowNodes([main1, main2, setup, teardown], 'main', [main2, main1])

    expect(merged.map((item) => `${item.phase}:${item.order}`)).toEqual([
      'setup:1',
      'main:1',
      'main:2',
      'teardown:1',
    ])
    expect(merged.map((item) => item.key)).toEqual(['setup-1', 'main-2', 'main-1', 'teardown-1'])
  })

  it('removes a node dragged from another phase instead of duplicating it', () => {
    const setup = node('setup-1', 'setup', 1)
    const main = node('main-1', 'main', 1)

    const merged = mergeFlowNodes([setup, main], 'main', [main, { ...setup, phase: 'setup', order: 9 }])

    expect(merged.map((item) => item.key)).toEqual(['main-1', 'setup-1'])
    expect(merged.map((item) => `${item.phase}:${item.order}`)).toEqual(['main:1', 'main:2'])
  })

  it('removes a deleted node when the edited phase is copied by the parent', () => {
    const setup = node('setup-1', 'setup', 1)
    const main1 = node('main-1', 'main', 1)
    const main2 = node('main-2', 'main', 2)
    const teardown = node('teardown-1', 'teardown', 1)

    const merged = mergeFlowNodes(
      [setup, main1, main2, teardown],
      'main',
      [{ ...main1, order: 1 }],
    )

    expect(merged.map((item) => item.key)).toEqual(['setup-1', 'main-1', 'teardown-1'])
  })

  it('normalizes every phase independently before saving', () => {
    const normalized = normalizeFlowNodeOrders([
      node('main-1', 'main', 4),
      node('setup-1', 'setup', 8),
      node('main-2', 'main', 9),
      node('teardown-1', 'teardown', 3),
    ])

    expect(normalized.map((item) => `${item.phase}:${item.order}`)).toEqual([
      'setup:1',
      'main:1',
      'main:2',
      'teardown:1',
    ])
  })
})
