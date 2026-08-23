<script setup lang="ts">
import { ref } from 'vue'

import type { Execution } from '@/api/executions'
import { useDeviceSelect, type RunTarget } from '@/composables/useDeviceSelect'

// V2 §5.12：统一设备选择器（所有运行入口复用，含重试入口）。
// 通过 ref.open(target, options) 触发；始终弹出设备选择弹窗并预选当前用户默认设备；
// open 的 Promise 保持 pending 直到弹窗流程结束
// （运行成功 resolve 执行对象 / 取消 resolve null），父级 await 后统一跳转/提示。
const emit = defineEmits<{ created: [execution: Execution] }>()

const timeout = ref(1800)
const { dialogVisible, devices, selectedId, setAsDefault, running, reason, open, confirmRun, close } =
  useDeviceSelect()

/** 弹窗路径的 open() 挂起解析器（成功/取消时唤醒父级 await）。 */
let openResolve: ((exec: Execution | null) => void) | null = null

function settleOpen(exec: Execution | null) {
  openResolve?.(exec)
  openResolve = null
}

async function run() {
  const exec = await confirmRun({ timeout_seconds: timeout.value })
  if (exec) {
    settleOpen(exec)
    emit('created', exec)
  }
}

function cancel() {
  settleOpen(null)
  close()
}

function onClosed() {
  // 弹窗被关闭（取消/遮罩/X/运行成功后的收起）→ 挂起的 open() 以 null 结束
  settleOpen(null)
  close()
}

defineExpose({
  /**
   * 始终弹出设备选择弹窗（预选用户默认设备）；
   * 运行成功 resolve Execution、取消 resolve null。
   */
  open: (target: RunTarget, options: { timeout_seconds?: number } = {}): Promise<Execution | null> => {
    if (options.timeout_seconds) timeout.value = options.timeout_seconds
    return new Promise((resolve) => {
      openResolve = resolve
      open(target, { timeout_seconds: options.timeout_seconds }).then((exec) => {
        if (exec !== null) {
          // 保留兜底：composable 直跑成功时直接返回
          openResolve = null
          resolve(exec)
        }
        // exec === null → 弹窗已打开，等待 run()/cancel()/@closed 收敛
      })
    })
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
    append-to-body
    @closed="onClosed"
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
      <el-button @click="cancel">取消</el-button>
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