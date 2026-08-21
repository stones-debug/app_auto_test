import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/agents', () => ({
  getDefaultDevice: vi.fn(),
  listDevices: vi.fn(),
  setDefaultDevice: vi.fn(),
}))
vi.mock('@/api/executions', () => ({
  createCaseExecution: vi.fn(),
  createSuiteExecution: vi.fn(),
}))

import { getDefaultDevice, listDevices, setDefaultDevice } from '@/api/agents'
import { createCaseExecution } from '@/api/executions'
import { apiErrorCode, useDeviceSelect } from '@/composables/useDeviceSelect'

const mGetDefault = vi.mocked(getDefaultDevice)
const mListDevices = vi.mocked(listDevices)
const mSetDefault = vi.mocked(setDefaultDevice)
const mCreateCase = vi.mocked(createCaseExecution)

const IDLE = { id: 11, status: 'idle', name: '设备A', platform: 'android', agent_id: 1, connection_type: 'usb' }
const BUSY = { id: 22, status: 'busy', name: '设备B', platform: 'android', agent_id: 1, connection_type: 'usb' }

function conflictError(code: string) {
  return { response: { data: { detail: { code } } } }
}

describe('useDeviceSelect（Windows 方案 §4.2 自动选机）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mListDevices.mockResolvedValue({ total: 2, page: 1, page_size: 200, items: [IDLE, BUSY] } as never)
    mCreateCase.mockResolvedValue({ id: 100 } as never)
  })

  it('默认设备可用 → 直接创建执行，不弹窗', async () => {
    mGetDefault.mockResolvedValue({
      device_id: 11,
      device: IDLE,
      available: true,
      reason: '',
    } as never)
    const select = useDeviceSelect()
    const created = await select.open(
      { kind: 'case', id: 1, name: '用例' },
      { timeout_seconds: 1800 },
    )
    expect(created).toBe(true)
    expect(select.dialogVisible.value).toBe(false)
    expect(mCreateCase).toHaveBeenCalledWith(1, { timeout_seconds: 1800, device_id: 11 })
  })

  it('默认设备忙碌 → 弹窗选择，且只列空闲设备', async () => {
    mGetDefault.mockResolvedValue({
      device_id: 11,
      device: { ...IDLE, status: 'busy' },
      available: false,
      reason: '设备忙或已被占用',
    } as never)
    const select = useDeviceSelect()
    const created = await select.open(
      { kind: 'case', id: 1, name: '用例' },
      { timeout_seconds: 1800 },
    )
    expect(created).toBe(false)
    expect(select.dialogVisible.value).toBe(true)
    expect(mListDevices).toHaveBeenCalled()
    expect(select.devices.value.map((d) => d.id)).toEqual([11])
    expect(select.reason.value).toContain('忙')
  })

  it('未设置默认设备 → 弹窗选择', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '未设置默认设备' } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'suite', id: 2, name: '套件' }, {})
    expect(select.dialogVisible.value).toBe(true)
  })

  it('弹窗确认运行：携带 device_id，勾选后设为默认', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '' } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'case', id: 1, name: '用例' }, {})
    select.selectedId.value = 11
    select.setAsDefault.value = true
    const ok = await select.confirmRun({ timeout_seconds: 600 })
    expect(ok).toBe(true)
    expect(mSetDefault).toHaveBeenCalledWith(11)
    expect(mCreateCase).toHaveBeenCalledWith(1, { timeout_seconds: 600, device_id: 11 })
    expect(select.dialogVisible.value).toBe(false)
  })

  it('并发占用（DEVICE_BUSY）→ 提示并刷新，不自动换设备', async () => {
    mGetDefault.mockResolvedValue({ device_id: 11, device: IDLE, available: true, reason: '' } as never)
    mCreateCase.mockRejectedValue(conflictError('DEVICE_BUSY') as never)
    const select = useDeviceSelect()
    const created = await select.open(
      { kind: 'case', id: 1, name: '用例' },
      { timeout_seconds: 1800 },
    )
    // 默认设备被占 → 弹窗，不自动改用其他设备
    expect(created).toBe(false)
    expect(select.dialogVisible.value).toBe(true)
    expect(select.reason.value).toContain('占用')
    expect(mCreateCase).toHaveBeenCalledTimes(1)
  })

  it('无可用设备 → 不提交空 device_id（运行按钮禁用）', async () => {
    mGetDefault.mockResolvedValue({ device_id: null, device: null, available: false, reason: '' } as never)
    mListDevices.mockResolvedValue({ total: 0, page: 1, page_size: 200, items: [] } as never)
    const select = useDeviceSelect()
    await select.open({ kind: 'case', id: 1, name: '用例' }, {})
    expect(select.devices.value).toHaveLength(0)
    expect(select.selectedId.value).toBeNull()
    const ok = await select.confirmRun({})
    expect(ok).toBe(false)
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
