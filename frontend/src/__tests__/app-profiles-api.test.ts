import { describe, expect, it } from 'vitest'

import { apiErrorDetail, type ProfileRunParams } from '@/api/appProfiles'

describe('appProfiles API 契约', () => {
  it('apiErrorDetail 解析 code/message/context', () => {
    const error = {
      response: {
        data: { detail: { code: 'PROFILE_REVISION_CONFLICT', message: '版本已变化', context: { current: 18, expected: 17 } } },
      },
    }
    const detail = apiErrorDetail(error)
    expect(detail).not.toBeNull()
    expect(detail?.code).toBe('PROFILE_REVISION_CONFLICT')
    expect(detail?.context.current).toBe(18)
    expect(detail?.context.expected).toBe(17)
  })

  it('apiErrorDetail 拒绝字符串 detail，业务分支必须使用机器码', () => {
    const error = { response: { data: { detail: 'APP 档案不存在' } } }
    expect(apiErrorDetail(error)).toBeNull()
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

describe('appProfiles 套件前后置步骤契约', () => {
  it('suiteSteps 存在且 SkipTarget 支持 suite_step', async () => {
    const mod = await import('@/api/appProfiles')
    expect(typeof mod.suiteSteps).toBe('function')
    const target: import('@/api/appProfiles').SkipTarget = { type: 'suite_step', suite_id: 11, node_key: 'uuid' }
    expect(target.type).toBe('suite_step')
    expect(target.suite_id).toBe(11)
    expect(target.node_key).toBe('uuid')
  })
  it('suite-step 覆盖 API 存在', async () => {
    const mod = await import('@/api/appProfiles')
    expect(typeof mod.upsertSuiteStepOverride).toBe('function')
    expect(typeof mod.restoreSuiteStepOverride).toBe('function')
  })
})
