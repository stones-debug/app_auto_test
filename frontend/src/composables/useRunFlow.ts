import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useDeviceSelect, type RunTarget } from '@/composables/useDeviceSelect'

// V2 §6.1：统一运行流程——默认设备直跑 → 选择器；成功后跳执行中心。
export function useRunFlow() {
  const router = useRouter()
  const running = ref(false)
  const { open } = useDeviceSelect()

  /** 触发一次运行：返回 true 表示已直接创建执行（默认设备），否则选择弹窗已打开。 */
  async function run(target: RunTarget, options: { timeout_seconds?: number } = {}): Promise<boolean> {
    running.value = true
    try {
      const created = await open(target, options)
      if (created) router.push({ path: '/executions', query: { focus: 'new' } })
      return created
    } finally {
      running.value = false
    }
  }

  return { running, run }
}