import { beforeEach, describe, expect, it, vi } from 'vitest'

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

beforeEach(() => {
  pushMock.mockClear()
  routeMock.params = {}
  routeMock.meta = { workspace: 'global' }
})

describe('Step 5 三个重试入口统一走 DevicePicker（retry target）', () => {
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

  it('没有挂载 picker 时不抛错、不跳转', async () => {
    const { retry } = useExecutionRetry()
    const exec = await retry(1, 'x')
    expect(exec).toBeNull()
    expect(pushMock).not.toHaveBeenCalled()
  })
})
