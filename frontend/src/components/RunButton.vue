<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'

import DevicePicker from '@/components/DevicePicker.vue'
import { useWorkspaceNavigation } from '@/composables/useWorkspaceNavigation'

// V2 §5.12：运行按钮——默认设备可用直跑，否则打开 DevicePicker 选择。
// 方案 §5.7：从公共库点击运行时必须选择档案；targetId 供预检目标定位。
const props = defineProps<{ type: 'case' | 'suite'; id: number; name: string; disabled?: boolean; projectId?: number }>()
const router = useRouter()
const route = useRoute()
const navigation = useWorkspaceNavigation()

const picker = ref<InstanceType<typeof DevicePicker> | null>(null)
const running = ref(false)
const effectiveProjectId = props.projectId ?? Number(route.params.projectId)

async function onRun() {
  running.value = true
  try {
    const exec = await picker.value?.open(
      { kind: props.type, id: props.id, name: props.name },
      { targetId: props.id },
    )
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
    <DevicePicker ref="picker" :project-id="effectiveProjectId" />
  </span>
</template>
