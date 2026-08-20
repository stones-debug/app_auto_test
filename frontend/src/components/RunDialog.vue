<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { ElMessage } from 'element-plus'

import { createCaseExecution, createSuiteExecution } from '@/api/executions'
import { listDevices, type Device } from '@/api/agents'

const props = defineProps<{ type: 'case' | 'suite'; id: number; name: string }>()

const router = useRouter()
const visible = ref(false)
const devices = ref<Device[]>([])
const deviceId = ref<number | null>(null)
const timeout = ref(1800)
const running = ref(false)

async function loadDevices() {
  const data = await listDevices({ page_size: 100 })
  devices.value = data.items.filter((d) => d.status === 'idle')
  if (deviceId.value == null && devices.value.length) {
    deviceId.value = devices.value[0].id
  }
}

function open() {
  visible.value = true
  loadDevices()
}

async function run() {
  running.value = true
  try {
    const opts = { device_id: deviceId.value, timeout_seconds: timeout.value }
    const exec =
      props.type === 'case'
        ? await createCaseExecution(props.id, opts)
        : await createSuiteExecution(props.id, opts)
    ElMessage.success(`执行 #${exec.id} 已创建`)
    visible.value = false
    router.push({ path: '/executions', query: { focus: String(exec.id) } })
  } finally {
    running.value = false
  }
}

defineExpose({ open })
</script>

<template>
  <el-dialog v-model="visible" :title="`运行${type === 'case' ? '用例' : '套件'}：${name}`" width="420px">
    <el-form label-width="80px">
      <el-form-item label="设备">
        <el-select v-model="deviceId" placeholder="选择设备" class="w-full">
          <el-option v-for="d in devices" :key="d.id" :label="`${d.name} (${d.platform})`" :value="d.id" />
        </el-select>
        <div v-if="!devices.length" class="no-device">当前无可用的空闲设备，请先在设备管理页确认 Agent 在线。</div>
      </el-form-item>
      <el-form-item label="超时(s)">
        <el-input-number v-model="timeout" :min="60" :max="7200" :step="60" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="running" :disabled="!deviceId" @click="run">运行</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.w-full {
  width: 100%;
}
.no-device {
  color: #e6a23c;
  font-size: 12px;
  margin-top: 6px;
}
</style>
