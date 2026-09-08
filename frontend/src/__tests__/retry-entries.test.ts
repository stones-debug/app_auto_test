import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// 三个重试入口（执行列表/执行详情/报告详情）共用 useExecutionRetry：
// 选机成功后跳新 execution 详情。此处注入假 picker，断言跳转目标与透传参数。
const pushMock = vi.fn()
const routeMock = {
  params: {} as Record<string, string>,
  meta: { workspace: 'global' } as Record<string, unknown>,
}
vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ push: pushMock, replace: vi.fn(), back: vi.fn() }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))

import { useExecutionRetry } from '@/composables/useExecutionRetry'
import { canRetryExecution, resolveRetryTimeout } from '@/api/executions'

const devicePickerSource = readFileSync(
  resolve(process.cwd(), 'src/components/DevicePicker.vue'),
  'utf8',
)

beforeEach(() => {
  pushMock.mockClear()
  routeMock.params = {}
  routeMock.meta = { workspace: 'global' }
})

describe('Step 5 三个重试入口统一走 DevicePicker（retry target）', () => {
  it('只有终态允许显示重试入口', () => {
    expect(['passed', 'failed', 'error', 'stopped', 'cancelled'].every(canRetryExecution)).toBe(true)
    expect(['queued', 'running', 'stopping'].some(canRetryExecution)).toBe(false)
  })

  it('重试设备选择不加载当前 APP 档案或重新预检', () => {
    expect(devicePickerSource).not.toContain('getAppProfile')
    expect(devicePickerSource).toContain("if (!options.profile && target.kind !== 'retry' && props.projectId != null)")
    expect(devicePickerSource).toContain('重试将沿用原执行快照')
  })

  it('重试 timeout 默认沿用原执行，显式值优先', () => {
    expect(resolveRetryTimeout(420)).toBe(420)
    expect(resolveRetryTimeout(420, 600)).toBe(600)
    expect(resolveRetryTimeout(null)).toBe(1800)
  })

  it('重试成功后跳新 execution 详情', async () => {
    const { picker, retry } = useExecutionRetry()
    picker.value = {
      open: vi.fn().mockResolvedValue({ id: 42, status: 'queued' }),
    }
    const exec = await retry(7, '执行 #7')
    expect(exec?.id).toBe(42)
    expect(pushMock).toHaveBeenCalledWith({ name: 'ExecutionDetail', params: { executionId: 42 } })
  })

  it('项目工作区重试后进入同项目的新执行详情', async () => {
    routeMock.params = { projectId: '9' }
    routeMock.meta = { workspace: 'project' }
    const { picker, retry } = useExecutionRetry()
    picker.value = {
      open: vi.fn().mockResolvedValue({ id: 43, project_id: 9, status: 'queued' }),
    }

    await retry(7, '执行 #7')

    expect(pushMock).toHaveBeenCalledWith({
      name: 'ProjectExecutionDetail',
      params: { projectId: 9, executionId: 43 },
    })
  })

  it('以 retry target 传给 DevicePicker（executionId 不伪装成 case id）', async () => {
    const { picker, retry } = useExecutionRetry()
    const open = vi.fn().mockResolvedValue(null) // 默认设备不可用 → 弹窗
    picker.value = { open }
    const exec = await retry(99, '执行 #99')
    expect(exec).toBeNull()
    expect(open).toHaveBeenCalledWith(
      { kind: 'retry', executionId: 99, name: '执行 #99' },
    )
    expect(pushMock).not.toHaveBeenCalled()
  })

  it('重试入口把显式 timeout 传给 DevicePicker', async () => {
    const { picker, retry } = useExecutionRetry()
    const open = vi.fn().mockResolvedValue(null)
    picker.value = { open }
    await retry(100, '执行 #100', { timeout_seconds: 600 })
    expect(open).toHaveBeenCalledWith(
      { kind: 'retry', executionId: 100, name: '执行 #100' },
      { timeout_seconds: 600 },
    )
  })

  it('没有挂载 picker 时不抛错、不跳转', async () => {
    const { retry } = useExecutionRetry()
    const exec = await retry(1, 'x')
    expect(exec).toBeNull()
    expect(pushMock).not.toHaveBeenCalled()
  })
})
