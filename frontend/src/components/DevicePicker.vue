<script setup lang="ts">
import { ref } from 'vue'

import { useDeviceSelect, type RunTarget } from '@/composables/useDeviceSelect'

// V2 §5.12：统一设备选择器（所有运行入口复用）。
// 通过 ref.open(target, options) 触发；默认设备可用时直接创建执行返回 true，
// 否则打开选择弹窗。选中运行成功通过 emit('created') 通知父级跳转。
const emit = defineEmits<{ created: [] }>()

const timeout = ref(1800)
const { dialogVisible, devices, selectedId, setAsDefault, running, reason, open, confirmRun, close } =
  useDeviceSelect()

async function run() {
  const ok = await confirmRun({ timeout_seconds: timeout.value })
  if (ok) emit('created')
}

defineExpose({
  /** 返回是否已直接创建执行（默认设备直跑）。未创建时选择弹窗已打开。 */
  open: (target: RunTarget, options: { timeout_seconds?: number } = {}) => {
    if (options.timeout_seconds) timeout.value = options.timeout_seconds
    return open(target, { timeout_seconds: options.timeout_seconds })
  },
  close,
})
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    title="选择设备运行"
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
          当前没有可用的在线空闲设备。请先在「设备中心」下载安装 Agent 并绑定。
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