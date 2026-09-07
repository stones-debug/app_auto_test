import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import type { Execution } from '@/api/executions'
import type { RetryTarget } from '@/composables/useDeviceSelect'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'

export type PickerLike = {
  open: (target: RetryTarget, options?: { timeout_seconds?: number }) => Promise<Execution | null>
}

export type RetryOpenOptions = { timeout_seconds?: number }

/**
 * Step 5：执行/报告页面的统一重试入口——经 DevicePicker 选机后创建重试执行，
 * 成功后跳转新 execution 详情。三个入口（执行列表/执行详情/报告详情）共用。
 */
export function useExecutionRetry() {
  const router = useRouter()
  const navigation = useWorkspaceNavigation()
  const picker = ref<PickerLike | null>(null)
  const running = ref(false)

  async function retry(
    executionId: number,
    name: string,
    options?: RetryOpenOptions,
  ): Promise<Execution | null> {
    if (!picker.value) return null
    running.value = true
    try {
      const target = { kind: 'retry' as const, executionId, name }
      const exec = options
        ? await picker.value.open(target, options)
        : await picker.value.open(target)
      if (exec) {
        ElMessage.success(`已创建重试执行 #${exec.id}`)
        await router.push(navigation.executionDetail(exec.id))
      }
      return exec
    } finally {
      running.value = false
    }
  }

  return { picker, running, retry }
}
