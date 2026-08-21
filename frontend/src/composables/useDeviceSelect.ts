import { ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getDefaultDevice, listDevices, setDefaultDevice, type Device } from '@/api/agents'
import {
  createCaseExecution,
  createSuiteExecution,
  retryExecution,
  type Execution,
  type RunOptions,
} from '@/api/executions'

export type RunTarget =
  | { kind: 'case'; id: number; name: string }
  | { kind: 'suite'; id: number; name: string }
  | { kind: 'retry'; executionId: number; name: string }

export type RetryTarget = Extract<RunTarget, { kind: 'retry' }>

/** 从 axios 错误中提取后端业务码（detail.code，如 DEVICE_REQUIRED / DEVICE_BUSY / AGENT_OFFLINE）。 */
export function apiErrorCode(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail
  return detail?.code ?? null
}

export const CONFLICT_CODES = new Set(['DEVICE_BUSY', 'AGENT_OFFLINE', 'DEVICE_REQUIRED'])

/**
 * Windows 方案 §4.2：运行入口自动选机，供「普通运行 / 重试」三类入口统一复用。
 * 1) 默认设备可用 → 直接创建执行（返回 Execution）；2) 不可用 → 弹窗选择，返回 null；
 * 3) 无可选 → 引导；4) 并发占用 → 提示并刷新，不自动换设备。
 */
export function useDeviceSelect() {
  const dialogVisible = ref(false)
  const devices = ref<Device[]>([])
  const selectedId = ref<number | null>(null)
  const setAsDefault = ref(false)
  const running = ref(false)
  const reason = ref('')
  let target: RunTarget | null = null

  function loadDevices() {
    return listDevices({ page_size: 200 }).then((data) => {
      devices.value = data.items.filter((d) => d.status === 'idle')
      if (!devices.value.some((d) => d.id === selectedId.value)) {
        selectedId.value = devices.value[0]?.id ?? null
      }
    })
  }

  async function doRun(deviceId: number, options: RunOptions): Promise<Execution> {
    if (!target) throw new Error('未设置运行目标')
    const opts: RunOptions = { ...options, device_id: deviceId }
    if (target.kind === 'case') return createCaseExecution(target.id, opts)
    if (target.kind === 'suite') return createSuiteExecution(target.id, opts)
    // retry：后端仅接受 {device_id, timeout_seconds?}，不携带 parameters
    return retryExecution(target.executionId, {
      device_id: deviceId,
      timeout_seconds: options.timeout_seconds,
    })
  }

  /** 入口：优先默认设备直跑；不可用时打开选择弹窗。直接创建成功返回 Execution，否则返回 null。 */
  async function open(t: RunTarget, options: RunOptions): Promise<Execution | null> {
    target = t
    reason.value = ''
    try {
      const def = await getDefaultDevice()
      if (def.available && def.device_id != null) {
        try {
          return await doRun(def.device_id, options)
        } catch (error) {
          if (CONFLICT_CODES.has(apiErrorCode(error) ?? '')) {
            // 默认设备刚被占用 → 弹窗选择，不自动改用其他设备
            reason.value = '设备刚被其他执行占用'
          } else {
            throw error
          }
        }
      } else {
        reason.value = def.reason || ''
      }
    } catch {
      reason.value = ''
    }
    await loadDevices()
    dialogVisible.value = true
    return null
  }

  /** 弹窗内确认运行。并发占用时刷新列表并提示，返回创建的 Execution 或 null。 */
  async function confirmRun(options: RunOptions): Promise<Execution | null> {
    if (selectedId.value == null) return null
    running.value = true
    try {
      if (setAsDefault.value) {
        try {
          await setDefaultDevice(selectedId.value)
        } catch {
          /* 设为默认失败不阻断运行 */
        }
      }
      try {
        const exec = await doRun(selectedId.value, options)
        dialogVisible.value = false
        setAsDefault.value = false
        return exec
      } catch (error) {
        if (CONFLICT_CODES.has(apiErrorCode(error) ?? '')) {
          ElMessage.warning('设备刚被其他执行占用，已刷新选择列表')
          await loadDevices()
          return null
        }
        throw error
      }
    } finally {
      running.value = false
    }
  }

  function close() {
    dialogVisible.value = false
    setAsDefault.value = false
  }

  return {
    dialogVisible,
    devices,
    selectedId,
    setAsDefault,
    running,
    reason,
    open,
    confirmRun,
    loadDevices,
    close,
    apiErrorCode,
  }
}

export type DeviceSelect = ReturnType<typeof useDeviceSelect>