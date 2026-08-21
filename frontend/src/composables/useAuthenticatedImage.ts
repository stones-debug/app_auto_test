import { onBeforeUnmount, ref } from 'vue'

import { getToken } from '@/utils/request'

// V2 §6.1：鉴权图片 Blob 生命周期（与 AuthenticatedImage 组件同逻辑，供脚本场景复用）。
export function useAuthenticatedImage() {
  const objectUrl = ref<string | null>(null)
  const failed = ref(false)
  const loading = ref(false)

  async function load(src: string) {
    if (!src) return
    failed.value = false
    loading.value = true
    if (objectUrl.value) {
      URL.revokeObjectURL(objectUrl.value)
      objectUrl.value = null
    }
    try {
      const token = getToken()
      const resp = await fetch(src, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      if (!resp.ok) throw new Error(`加载失败: ${resp.status}`)
      const blob = await resp.blob()
      if (blob.size === 0) throw new Error('空文件')
      objectUrl.value = URL.createObjectURL(blob)
    } catch {
      failed.value = true
    } finally {
      loading.value = false
    }
  }

  onBeforeUnmount(() => {
    if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  })

  return { objectUrl, failed, loading, load }
}