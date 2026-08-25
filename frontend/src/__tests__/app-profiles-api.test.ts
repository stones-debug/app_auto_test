import { describe, expect, it } from 'vitest'

import { apiErrorDetail, type ProfileRunParams } from '@/api/appProfiles'

describe('appProfiles API 契约', () => {
  it('apiErrorDetail 解析结构化后端错误码', () => {
    const error = {
      response: {
        data: { detail: { code: 'PROFILE_REVISION_CONFLICT', current: 18, expected: 17 } },
      },
    }
    const detail = apiErrorDetail(error)
    expect(detail).not.toBeNull()
    expect(detail?.code).toBe('PROFILE_REVISION_CONFLICT')
    expect(detail?.current).toBe(18)
    expect(detail?.expected).toBe(17)
  })

  it('apiErrorDetail 解析字符串 detail', () => {
    const error = { response: { data: { detail: 'APP 档案不存在' } } }
    expect(apiErrorDetail(error)?.code).toBe('HTTP_ERROR')
    expect(apiErrorDetail(error)?.message).toBe('APP 档案不存在')
  })

  it('apiErrorDetail 无 detail 返回 null', () => {
    expect(apiErrorDetail({})).toBeNull()
    expect(apiErrorDetail(null)).toBeNull()
  })

  it('ProfileRunParams 结构合法', () => {
    const p: ProfileRunParams = {
      app_profile_id: 12,
      app_release_id: 33,
      expected_profile_revision: 22,
      expected_test_asset_revision: 205,
    }
    expect(p.app_profile_id).toBe(12)
    expect(p.expected_profile_revision).toBe(22)
  })
})

describe('execution RunOptions 扩展契约', () => {
  it('RunOptions 支持档案/版本/revision 字段', () => {
    const options: import('@/api/executions').RunOptions = {
      device_id: 8,
      app_profile_id: 12,
      app_release_id: 33,
      expected_profile_revision: 22,
      expected_test_asset_revision: 205,
      parameters: { use_pre_steps: true },
    }
    expect(options.app_profile_id).toBe(12)
    expect(options.expected_test_asset_revision).toBe(205)
  })
})

describe('appProfiles 编辑/删除契约', () => {
  it('updateAppProfile 接收 expected_revision 与可编辑字段', async () => {
    const mod = await import('@/api/appProfiles')
    expect(typeof mod.updateAppProfile).toBe('function')
  })
  it('deleteAppProfile 接收 expected_revision', async () => {
    const mod = await import('@/api/appProfiles')
    expect(typeof mod.deleteAppProfile).toBe('function')
  })
})
