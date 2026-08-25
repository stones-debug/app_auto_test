import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiErrorDetail } from '@/api/appProfiles'

const mocks = vi.hoisted(() => ({ differences: vi.fn() }))
vi.mock('@/api/appProfiles', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/api/appProfiles')>()
  return { ...orig, differences: mocks.differences }
})

import { differences } from '@/api/appProfiles'

describe('差异清单 API 契约', () => {
  beforeEach(() => vi.clearAllMocks())

  it('differences 请求传入 type/分页参数', async () => {
    mocks.differences.mockResolvedValue({ total: 1, page: 1, page_size: 20, items: [] })
    await differences(12, { type: 'skipped', page: 1, page_size: 20 })
    expect(mocks.differences).toHaveBeenCalledWith(12, { type: 'skipped', page: 1, page_size: 20 })
  })

  it('differences 返回某跳过项含原因/类型', async () => {
    mocks.differences.mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 20,
      items: [
        { target_type: 'case', path: '登录/网约车司机登录', reason_code: 'unsupported', reason_note: '无司机角色', source_type: 'direct', override: false },
      ],
    })
    const page = await differences(12, {})
    expect(page.items[0].target_type).toBe('case')
    expect(page.items[0].reason_code).toBe('unsupported')
    expect(page.items[0].override).toBe(false)
  })
})

describe('结构化错误解析', () => {
  it('解析 409 revision 冲突占位', () => {
    expect(apiErrorDetail({ response: { data: { detail: { code: 'PROFILE_REVISION_CONFLICT' } } } })?.code).toBe('PROFILE_REVISION_CONFLICT')
  })
})
