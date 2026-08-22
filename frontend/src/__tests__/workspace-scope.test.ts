import { describe, expect, it } from 'vitest'

import { withProjectScope } from '@/navigation/workspaceScope'

describe('项目执行/报告查询作用域', () => {
  it('项目工作区固定附加 project_id', () => {
    expect(withProjectScope(7, { page: 2, status: 'failed' })).toEqual({
      page: 2,
      status: 'failed',
      project_id: 7,
    })
  })

  it('全局工作区不发送 project_id', () => {
    expect(withProjectScope(null, { page: 1 })).toEqual({ page: 1 })
  })
})
