import { describe, expect, it } from 'vitest'

import { buildExclusionTree } from '@/utils/exclusionTree'

describe('exclusionTree occurrence identity', () => {
  it('同套件同用例的不同 occurrence 保持独立节点和唯一 key', () => {
    const tree = buildExclusionTree([
      {
        target_type: 'case', suite_id: 7, case_id: 11, occurrence_order: 1,
        path: '套件/用例', reason_code: 'unsupported', reason_note: null, source_type: 'direct',
      },
      {
        target_type: 'case', suite_id: 7, case_id: 11, occurrence_order: 2,
        path: '套件/用例', reason_code: 'unsupported', reason_note: null, source_type: 'direct',
      },
    ])

    expect(tree).toHaveLength(1)
    expect(tree[0].children).toHaveLength(2)
    expect(new Set(tree[0].children.map((item) => item.key)).size).toBe(2)
    expect(tree[0].children.flatMap((item) => item.children)).toHaveLength(2)
  })
})
