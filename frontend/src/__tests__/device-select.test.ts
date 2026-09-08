import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('element-plus', () => ({
  ElMessage: { warning: vi.fn(), success: vi.fn() },
}))
vi.mock('@/api/agents', () => ({
  getDefaultDevice: vi.fn(),
  listDevices: vi.fn(),
  setDefaultDevice: vi.fn(),
}))
vi.mock('@/api/executions', () => ({
  createBatchExecution: vi.fn(),
  createCaseExecution: vi.fn(),
  createSuiteExecution: vi.fn(),
  retryExecution: vi.fn(),
}))

import { getDefaultDevice, listDevices, setDefaultDevice } from '@/api/agents'
import { createBatchExecution, createCaseExecution, createSuiteExecution, retryExecution } from '@/api/executions'
import { apiErrorCode, buildRunParameters, profileReleaseOption, useDeviceSelect } from '@/composables/useDeviceSelect'

const mGetDefault = vi.mocked(getDefaultDevice)
const mListDevices = vi.mocked(listDevices)
const mSetDefault = vi.mocked(setDefaultDevice)
const mCreateCase = vi.mocked(createCaseExecution)
const mCreateBatch = vi.mocked(createBatchExecution)
const mCreateSuite = vi.mocked(createSuiteExecution)
const mRetry = vi.mocked(retryExecution)

const IDLE = { id: 11, status: 'idle', name: '设备A', platform: 'android', agent_id: 1, connection_type: 'usb' }
const BUSY = { id: 22, status: 'busy', name: '设备B', platform: 'android', agent_id: 1, connection_type: 'usb' }

function conflictError(code: string) {
  return { response: { data: { detail: { code } } } }
}

describe('ProfileRunContext release display', () => {
  it('creates a stable readonly release option from the passed context', () => {
    expect(profileReleaseOption({
      app_profile_id: 12,
      app_release_id: 34,
      app_release_version: '2026.09',
      expected_profile_revision: 2,
      expected_test_asset_revision: 3,
    })).toEqual({ id: 34, version: '2026.09' })
  })
})

describe('useDeviceSelect（Windows 方案 §4.2 自动选机）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mListDevices.mockResolvedValue({ total: 2, page: 1, page_size: 200, items: [IDLE, BUSY] } as never)
    mCreateCase.mockResolvedValue({ id: 100, status: 'queued' } as never)
    mCreateSuite.mockResolvedValue({ id: 200, status: 'queued' } as never)
    mCreateBatch.mockResolvedValue({ id: 250, status: 'queued' } as never)
    mRetry.mockResolvedValue({ id: 300, status: 'queued' } as never)
  })

  it('case：默认设备可用 → 仍弹窗选择，且预选默认设备（不直跑）', async () => {
    mGetDefault.mockResolvedValue({
      device_id: 11,
      device: IDLE,
      available: true,
      reason: '',
    } as never)
    const select = useDeviceSelect()
    const exec = await select.open({ kind: 'case', id: 1, name: '用例' }, { timeout_seconds: 1800 })
    expect(exec).toBeNull()
    expect(select.dialogVisible.value).toBe(true)
    expect(select.selectedId.value).toBe(11)
    expect(mCreateCase).not.toHaveBeenCalled()
  })

  it('suite：默认设备可用 → 弹窗并预选默认设备，不直跑', async () => {
    mGetDefault.mockResolvedValue({
      device_id: 11,
      device: IDLE,
      available: true,
      reason: '',
    } as never)
    const select = useDeviceSelect()
    const exec = await select.open({ kind: 'suite', id: 2, name: '套件' }, {})
    expect(exec).toBeNull()
    expect(select.dialogVisible.value).toBe(true)
    expect(select.selectedId.value).toBe(11)
    expect(mCreateSuite).not.toHaveBeenCalled()
  })

  it('batch：确认运行时提交全部 suite_ids', async () => {
    mGetDefault.mockResolvedValue({ device_id: 11, device: IDLE, available: true, reason: '' } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'batch', suiteIds: [2, 5, 8], name: '全部套件' })

    const execution = await select.confirmRun({ timeout_seconds: 1200 })

    expect(execution?.id).toBe(250)
    expect(mCreateBatch).toHaveBeenCalledWith({
      suite_ids: [2, 5, 8],
      timeout_seconds: 1200,
      device_id: 11,
    })
  })

  it('profile_all batch：不由客户端收集 suite_ids，交给服务端确定项目套件', async () => {
    mGetDefault.mockResolvedValue({ device_id: 11, device: IDLE, available: true, reason: '' } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'batch', suiteIds: [], targetScope: 'profile_all', name: '当前 APP 全部套件' })

    await select.confirmRun({ timeout_seconds: 1200 })

    expect(mCreateBatch).toHaveBeenCalledWith({
      suite_ids: [],
      target_scope: 'profile_all',
      timeout_seconds: 1200,
      device_id: 11,
    })
  })

  it('retry：弹窗预选默认设备；确认运行调用 {device_id, timeout_seconds?}，不伪装成 case/suite', async () => {
    mGetDefault.mockResolvedValue({
      device_id: 11,
      device: IDLE,
      available: true,
      reason: '',
    } as never)
    const select = useDeviceSelect()
    const exec = await select.open(
      { kind: 'retry', executionId: 333, name: '执行 #333' },
      { timeout_seconds: 600 },
    )
    expect(exec).toBeNull()
    expect(select.selectedId.value).toBe(11)
    const run = await select.confirmRun({ timeout_seconds: 600 })
    expect(run?.id).toBe(300)
    expect(mRetry).toHaveBeenCalledWith(333, { device_id: 11, timeout_seconds: 600 })
    expect(mCreateCase).not.toHaveBeenCalled()
    expect(mCreateSuite).not.toHaveBeenCalled()
  })

  it('默认设备忙碌 → 弹窗选择，且只列空闲设备，预选回退首个空闲设备', async () => {
    mGetDefault.mockResolvedValue({
      device_id: 11,
      device: { ...IDLE, status: 'busy' },
      available: false,
      reason: '设备忙或已被占用',
    } as never)
    const select = useDeviceSelect()
    const exec = await select.open(
      { kind: 'case', id: 1, name: '用例' },
      { timeout_seconds: 1800 },
    )
    expect(exec).toBeNull()
    expect(select.dialogVisible.value).toBe(true)
    expect(mListDevices).toHaveBeenCalled()
    expect(select.devices.value.map((d) => d.id)).toEqual([11])
    // 默认设备不在空闲列表 → 回退第一个空闲设备
    expect(select.selectedId.value).toBe(11)
    expect(select.reason.value).toContain('忙')
  })

  it('未设置默认设备 → 弹窗选择，预选列表第一个设备', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '未设置默认设备' } as never)
    const select = useDeviceSelect()
    const exec = await select.open({ kind: 'suite', id: 2, name: '套件' }, {})
    expect(exec).toBeNull()
    expect(select.dialogVisible.value).toBe(true)
    expect(select.selectedId.value).toBe(11)
  })

  it('弹窗确认运行：携带 device_id，勾选后设为默认，返回 Execution', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '' } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'case', id: 1, name: '用例' }, {})
    select.selectedId.value = 11
    select.setAsDefault.value = true
    const exec = await select.confirmRun({ timeout_seconds: 600 })
    expect(exec?.id).toBe(100)
    expect(mSetDefault).toHaveBeenCalledWith(11)
    expect(mCreateCase).toHaveBeenCalledWith(1, { timeout_seconds: 600, device_id: 11 })
    expect(select.dialogVisible.value).toBe(false)
  })

  it('弹窗确认运行遇并发占用（DEVICE_BUSY）→ 提示并刷新，不自动换设备，返回 null', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '' } as never)
    mCreateCase.mockRejectedValue(conflictError('DEVICE_BUSY') as never)
    const select = useDeviceSelect()
    await select.open(
      { kind: 'case', id: 1, name: '用例' },
      { timeout_seconds: 1800 },
    )
    select.selectedId.value = 11
    const exec = await select.confirmRun({ timeout_seconds: 1800 })
    expect(exec).toBeNull()
    // 弹窗保持打开，等待用户重新选择
    expect(select.dialogVisible.value).toBe(true)
    expect(mCreateCase).toHaveBeenCalledTimes(1)
  })

  it('无可用设备 → 不提交空 device_id（运行按钮禁用）', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '' } as never)
    mListDevices.mockResolvedValue({ total: 0, page: 1, page_size: 200, items: [] } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'case', id: 1, name: '用例' }, {})
    expect(select.devices.value).toHaveLength(0)
    expect(select.selectedId.value).toBeNull()
    const exec = await select.confirmRun({})
    expect(exec).toBeNull()
    expect(mCreateCase).not.toHaveBeenCalled()
  })
})

describe('apiErrorCode', () => {
  it('提取 detail.code', () => {
    expect(apiErrorCode(conflictError('DEVICE_REQUIRED'))).toBe('DEVICE_REQUIRED')
    expect(apiErrorCode(new Error('boom'))).toBeNull()
    expect(apiErrorCode({ response: { data: { detail: 'string' } } })).toBeNull()
  })
})

describe('运行阶段参数', () => {
  const settings = {
    use_pre_steps: true,
    use_post_steps: true,
    attach_to_current_app: true,
  }

  it('单用例携带前置、后置和当前界面模式', () => {
    expect(buildRunParameters('case', settings)).toEqual(settings)
  })

  it('套件不允许携带当前界面模式', () => {
    expect(buildRunParameters('suite', settings)).toEqual({
      use_pre_steps: true,
      use_post_steps: true,
    })
  })

  it('批量套件沿用套件阶段选项且不携带当前界面模式', () => {
    expect(buildRunParameters('batch', settings)).toEqual({
      use_pre_steps: true,
      use_post_steps: true,
    })
  })

  it('重试沿用原执行参数，不重新提交阶段选项', () => {
    expect(buildRunParameters('retry', settings)).toEqual({})
  })
})

describe('方案 §4.8 档案上下文（ProfileRunContext）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mListDevices.mockResolvedValue({ total: 1, page: 1, page_size: 200, items: [IDLE] } as never)
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '' } as never)
    mCreateCase.mockResolvedValue({ id: 100, status: 'queued' } as never)
  })

  it('open 传入 profile → 确认运行时合并 app_profile_id/双 revision 到创建参数', async () => {
    const select = useDeviceSelect()
    await select.open(
      { kind: 'case', id: 1, name: '用例' },
      {
        profile: {
          app_profile_id: 12,
          app_release_id: 33,
          app_release_version: '1.2.3',
          expected_profile_revision: 22,
          expected_test_asset_revision: 205,
        },
      },
    )
    select.selectedId.value = 11
    await select.confirmRun({ timeout_seconds: 600 })
    expect(mCreateCase).toHaveBeenCalledWith(1, {
      timeout_seconds: 600,
      device_id: 11,
      app_profile_id: 12,
      app_release_id: 33,
      expected_profile_revision: 22,
      expected_test_asset_revision: 205,
    })
  })

  it('无 profile 上下文时不注入档案字段', async () => {
    const select = useDeviceSelect()
    await select.open({ kind: 'case', id: 1, name: '用例' })
    select.selectedId.value = 11
    await select.confirmRun({ timeout_seconds: 600 })
    expect(mCreateCase).toHaveBeenCalledWith(1, { timeout_seconds: 600, device_id: 11 })
  })

  it('预检 token 会随档案上下文传给创建请求', async () => {
    const select = useDeviceSelect()
    await select.open(
      { kind: 'case', id: 1, name: '用例' },
      {
        profile: {
          app_profile_id: 12,
          app_release_id: 33,
          app_release_version: '1.2.3',
          expected_profile_revision: 22,
          expected_test_asset_revision: 205,
          prepare_token: 'opaque-prepare-token',
        },
      },
    )
    select.selectedId.value = 11
    await select.confirmRun({ timeout_seconds: 600 })
    expect(mCreateCase).toHaveBeenCalledWith(1, expect.objectContaining({
      prepare_token: 'opaque-prepare-token',
    }))
  })
})
