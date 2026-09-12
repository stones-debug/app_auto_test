import { ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getDefaultDevice, listDevices, setDefaultDevice, type Device } from '@/api/agents'
import { apiErrorDetail } from '@/utils/request'
import {
  createCaseExecution,
  createBatchExecution,
  createSuiteExecution,
  retryExecution,
  type Execution,
  type ExecutionRunSettings,
  type RunOptions,
} from '@/api/executions'

export type RunTarget =
  | { kind: 'case'; id: number; name: string; contextSuiteCaseId?: number }
  | { kind: 'suite'; id: number; name: string }
  | {
      kind: 'batch'
      suiteIds: number[]
      name: string
      targetScope?: 'explicit' | 'profile_all'
      /** profile_all 的本地取消补集；不分页收集全量 suite IDs。 */
      excludedSuiteIds?: number[]
    }
  | { kind: 'retry'; executionId: number; name: string }

export type RetryTarget = Extract<RunTarget, { kind: 'retry' }>

/** 方案 §4.8：运行创建的档案/版本/双 revision 上下文。 */
export interface ProfileRunContext {
  app_profile_id: number
  app_release_id: number
  app_release_version: string
  expected_profile_revision: number
  expected_test_asset_revision: number
  prepare_token?: string
}

export function profileReleaseOption(profile: ProfileRunContext): { id: number; version: string } {
  return { id: profile.app_release_id, version: profile.app_release_version }
}

/** 从 axios 错误中提取后端业务码（detail.code，如 DEVICE_REQUIRED / DEVICE_BUSY / AGENT_OFFLINE）。 */
export function apiErrorCode(error: unknown): string | null {
  return apiErrorDetail(error)?.code ?? null
}

export const CONFLICT_CODES = new Set(['DEVICE_BUSY', 'AGENT_OFFLINE', 'DEVICE_REQUIRED'])

export function buildRunParameters(
  kind: RunTarget['kind'],
  settings: ExecutionRunSettings,
): Record<string, unknown> {
  if (kind === 'retry') return {}
  const parameters: Record<string, unknown> = {
    use_pre_steps: settings.use_pre_steps,
    use_post_steps: settings.use_post_steps,
  }
  if (kind === 'case') parameters.attach_to_current_app = settings.attach_to_current_app
  return parameters
}

/**
 * Windows 方案 §4.2：运行入口选机，供单用例、单套件、批量套件和重试统一复用。
 * V2：始终弹出设备选择弹窗并预选当前用户默认设备（可用）；未设默认或默认不可用则回退首个空闲设备。
 * 弹窗内可临时切换设备、勾选"设为默认"；并发占用提示并刷新，不自动换设备。
 * 方案 §4.8：创建需携带档案/版本/双 revision（ProfileRunContext）。
 */
export function useDeviceSelect() {
  const dialogVisible = ref(false)
  const devices = ref<Device[]>([])
  const selectedId = ref<number | null>(null)
  const setAsDefault = ref(false)
  const running = ref(false)
  const reason = ref('')
  const targetKind = ref<RunTarget['kind'] | null>(null)
  let target: RunTarget | null = null
  let defaultDeviceId: number | null = null
  let profileCtx: ProfileRunContext | null = null

  function loadDevices() {
    return listDevices({ page_size: 200 }).then((data) => {
      devices.value = data.items.filter((d) => d.status === 'idle')
      // 优先预选用户默认设备；默认不可用/未设置时回退首个空闲设备
      if (defaultDeviceId != null && devices.value.some((d) => d.id === defaultDeviceId)) {
        selectedId.value = defaultDeviceId
      } else if (!devices.value.some((d) => d.id === selectedId.value)) {
        selectedId.value = devices.value[0]?.id ?? null
      }
    })
  }

  async function doRun(deviceId: number, options: RunOptions): Promise<Execution> {
    if (!target) throw new Error('未设置运行目标')
    const opts: RunOptions = { ...options, device_id: deviceId }
    // 方案 §4.8：合并档案/版本/双 revision
    if (profileCtx) {
      opts.app_profile_id = profileCtx.app_profile_id
      opts.app_release_id = profileCtx.app_release_id
      opts.expected_profile_revision = profileCtx.expected_profile_revision
      opts.expected_test_asset_revision = profileCtx.expected_test_asset_revision
      if (profileCtx.prepare_token) opts.prepare_token = profileCtx.prepare_token
    }
    if (target.kind === 'case' && target.contextSuiteCaseId != null) {
      opts.context_suite_case_id = target.contextSuiteCaseId
    }
    if (target.kind === 'case') return createCaseExecution(target.id, opts)
    if (target.kind === 'suite') return createSuiteExecution(target.id, opts)
    if (target.kind === 'batch') {
      return createBatchExecution({
        ...opts,
        suite_ids: target.suiteIds,
        ...(target.targetScope ? { target_scope: target.targetScope } : {}),
        ...(target.targetScope === 'profile_all'
          ? { excluded_suite_ids: [...new Set(target.excludedSuiteIds ?? [])].sort((a, b) => a - b) }
          : {}),
      })
    }
    // retry：后端仅接受 {device_id, timeout_seconds?}，档案由服务端按原执行读取
    return retryExecution(target.executionId, {
      device_id: deviceId,
      timeout_seconds: options.timeout_seconds,
    })
  }

  /** 入口：始终打开设备选择弹窗，并预选用户默认设备；直接创建返回 null（弹窗流程收敛）。 */
  async function open(t: RunTarget, options: { timeout_seconds?: number; profile?: ProfileRunContext } = {}): Promise<Execution | null> {
    void options.timeout_seconds // 保留签名兼容调用方；弹窗内确认运行时才应用超时等选项
    profileCtx = options.profile ?? null
    target = t
    targetKind.value = t.kind
    reason.value = ''
    try {
      const def = await getDefaultDevice()
      defaultDeviceId = def.device_id
      // 默认设备未设置/不可用（忙碌、离线）时展示原因，便于用户知晓预选回退
      if (!def.available) reason.value = def.reason ?? ''
    } catch {
      reason.value = ''
    }
    await loadDevices()
    dialogVisible.value = true
    return null
  }

  /** 弹窗内确认运行。并发占用时刷新列表并提示，返回创建的 Execution 或 null。 */
  async function confirmRun(options: RunOptions & { profile?: ProfileRunContext } = {}): Promise<Execution | null> {
    if (options.profile) profileCtx = options.profile
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
    targetKind,
    open,
    confirmRun,
    loadDevices,
    close,
    apiErrorCode,
  }
}

export type DeviceSelect = ReturnType<typeof useDeviceSelect>
