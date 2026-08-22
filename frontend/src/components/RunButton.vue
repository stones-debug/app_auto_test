<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import DevicePicker from '@/components/DevicePicker.vue'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'

// V2 §5.12：运行按钮——默认设备可用直跑，否则打开 DevicePicker 选择。
// 成功后保持既有产品行为：跳执行中心并聚焦新执行。
const props = defineProps<{ type: 'case' | 'suite'; id: number; name: string; disabled?: boolean }>()
const router = useRouter()
const navigation = useWorkspaceNavigation()

const picker = ref<InstanceType<typeof DevicePicker> | null>(null)
const running = ref(false)

async function onRun() {
  running.value = true
  try {
    const exec = await picker.value?.open({ kind: props.type, id: props.id, name: props.name })
    if (exec) {
      await router.push(navigation.executionDetail(exec.id))
    }
  } finally {
    running.value = false
  }
}
</script>

<template>
  <span>
    <el-button type="primary" size="small" :loading="running" :disabled="disabled" @click="onRun">
      运行
    </el-button>
    <DevicePicker ref="picker" />
  </span>
</template>
