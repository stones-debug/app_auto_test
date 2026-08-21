<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

import { getToken } from '@/utils/request'

// V2 §7.10：鉴权图片 Blob 加载。禁止把 Access Token 拼入普通 URL。
const props = defineProps<{ src: string; alt?: string }>()
const objectUrl = ref<string | null>(null)
const failed = ref(false)

let pending: Promise<void> | null = null

async function load() {
  if (!props.src) return
  failed.value = false
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = null
  }
  pending = (async () => {
    const token = getToken()
    const resp = await fetch(props.src, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok) throw new Error(`加载失败: ${resp.status}`)
    const blob = await resp.blob()
    if (blob.size === 0) throw new Error('空文件')
    objectUrl.value = URL.createObjectURL(blob)
  })().catch(() => {
    failed.value = true
  })
  await pending
}

watch(() => props.src, load, { immediate: true })

onBeforeUnmount(() => {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
})
</script>

<template>
  <el-image v-if="objectUrl" :src="objectUrl" :alt="alt ?? ''" fit="contain" class="auth-image" />
  <div v-else-if="failed" class="img-failed v2-aux">截图不可用</div>
  <div v-else class="img-loading v2-aux">加载中…</div>
</template>

<style scoped>
.auth-image {
  max-width: 100%;
}
.img-failed,
.img-loading {
  padding: 12px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: var(--radius-input);
}
</style>