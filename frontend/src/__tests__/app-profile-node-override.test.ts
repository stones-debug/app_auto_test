import { describe, expect, it } from 'vitest'

import {
  buildEffectiveNodeOverride,
  buildNodeOverrideDiff,
  unsupportedNodeOverrideFields,
} from '@/utils/appProfileNodeOverride'

describe('APP 档案节点覆盖编辑', () => {
  const source = {
    element_id: 10,
    params: { wait_timeout: 10, retry: 1 },
  }

  it('以公共节点参数预填并合并已有覆盖', () => {
    expect(buildEffectiveNodeOverride(source, {
      params: { wait_timeout: 20, retry: 1 },
    })).toEqual({
      element_id: 10,
      params: { wait_timeout: 20, retry: 1 },
    })
  })

  it('只生成相对公共参数变化的顶层补丁', () => {
    expect(buildNodeOverrideDiff(source, {
      element_id: 10,
      params: { retry: 1, wait_timeout: 20 },
    })).toEqual({
      params: { retry: 1, wait_timeout: 20 },
    })
  })

  it('字段顺序变化不会被误判为覆盖', () => {
    expect(buildNodeOverrideDiff(source, {
      params: { retry: 1, wait_timeout: 10 },
      element_id: 10,
    })).toEqual({})
  })

  it('识别不可覆盖字段', () => {
    expect(unsupportedNodeOverrideFields({ action: 'click', order: 2, params: {} }))
      .toEqual(['action', 'order'])
  })
})
