<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import DevicePicker from '@/components/DevicePicker.vue'

// V2 §5.12：运行按钮——默认设备可用直跑，否则打开 DevicePicker 选择。
const props = defineProps<{ type: 'case' | 'suite'; id: number; name: string; disabled?: boolean }>()
const router = useRouter()

const picker = ref<InstanceType<typeof DevicePicker> | null>(null)
const running = ref(false)

async function onRun() {
  running.value = true
  try {
    const created = await picker.value?.open({ kind: props.type, id: props.id, name: props.name })
    if (created) {
      router.push({ path: '/executions', query: { focus: 'new' } })
    }
  } finally {
    running.value = false
  }
}

function onCreated() {
  router.push({ path: '/executions', query: { focus: 'new' } })
}
</script>

<template>
  <span>
    <el-button type="primary" size="small" :loading="running" :disabled="disabled" @click="onRun">
      运行
    </el-button>
    <DevicePicker ref="picker" @created="onCreated" />
  </span>
</template>