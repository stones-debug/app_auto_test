<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useDeviceSelect } from '@/composables/useDeviceSelect'

const props = defineProps<{ type: 'case' | 'suite'; id: number; name: string }>()

const router = useRouter()
const timeout = ref(1800)
const { dialogVisible, devices, selectedId, setAsDefault, running, reason, open, confirmRun, close } =
  useDeviceSelect()

async function openRun() {
  const created = await open(
    { kind: props.type, id: props.id, name: props.name },
    { timeout_seconds: timeout.value },
  )
  if (created) {
    ElMessage.success('已使用默认设备创建执行')
    router.push({ path: '/executions', query: { focus: 'new' } })
  }
}

async function run() {
  const ok = await confirmRun({ timeout_seconds: timeout.value })
  if (ok) {
    ElMessage.success('执行已创建')
    router.push({ path: '/executions', query: { focus: 'new' } })
  }
}

defineExpose({ open: openRun })
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    :title="`运行${type === 'case' ? '用例' : '套件'}：${name}`"
    width="440px"
    :close-on-click-modal="false"
    @closed="close()"
  >
    <el-form label-width="80px">
      <el-form-item label="设备">
        <el-select v-model="selectedId" placeholder="选择设备" class="w-full">
          <el-option v-for="d in devices" :key="d.id" :label="`${d.name} (${d.platform})`" :value="d.id" />
        </el-select>
        <div v-if="reason" class="tip warn">{{ reason }}</div>
        <div v-if="!devices.length" class="tip">
          当前没有可用的在线空闲设备。请先：
          <ul>
            <li>在「设备管理」页下载并安装 Windows Agent；</li>
            <li>复制你的个人 Key 并在 Agent 中绑定；</li>
            <li>连接 USB / 无线 Android 设备。</li>
          </ul>
        </div>
      </el-form-item>
      <el-form-item label="设为默认">
        <el-checkbox v-model="setAsDefault">运行后记住此设备</el-checkbox>
      </el-form-item>
      <el-form-item label="超时(s)">
        <el-input-number v-model="timeout" :min="60" :max="7200" :step="60" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close()">取消</el-button>
      <el-button type="primary" :loading="running" :disabled="!selectedId" @click="run">运行</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.w-full {
  width: 100%;
}
.tip {
  color: #909399;
  font-size: 12px;
  margin-top: 6px;
  line-height: 1.7;
}
.tip.warn {
  color: #e6a23c;
}
</style>
