import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import type { Execution } from '@/api/executions'
import type { RetryTarget } from '@/composables/useDeviceSelect'

export type PickerLike = {
  open: (target: RetryTarget, options?: { timeout_seconds?: number }) => Promise<Execution | null>
}

/**
 * Step 5：执行/报告页面的统一重试入口——经 DevicePicker 选机后创建重试执行，
 * 成功后跳转新 execution 详情。三个入口（执行列表/执行详情/报告详情）共用。
 */
export function useExecutionRetry() {
  const router = useRouter()
  const picker = ref<PickerLike | null>(null)
  const running = ref(false)

  async function retry(executionId: number, name: string): Promise<Execution | null> {
    if (!picker.value) return null
    running.value = true
    try {
      const exec = await picker.value.open({ kind: 'retry', executionId, name })
      if (exec) {
        ElMessage.success(`已创建重试执行 #${exec.id}`)
        router.push(`/executions/${exec.id}`)
      }
      return exec
    } finally {
      running.value = false
    }
  }

  return { picker, running, retry }
}