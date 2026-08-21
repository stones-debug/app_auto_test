<script setup lang="ts">
import { ref } from 'vue'
import { ElMessageBox } from 'element-plus'

// V2 §4.3：危险操作二次确认（确认文案必须含对象名称与影响）
const props = defineProps<{
  confirmText?: string
  title?: string
  danger?: boolean
  loading?: boolean
}>()

const emit = defineEmits<{ confirm: [] }>()
const busy = ref(false)

async function onClick() {
  try {
    await ElMessageBox.confirm(
      props.confirmText ?? '确认执行该操作？此操作不可撤销。',
      props.title ?? '提示',
      { type: 'warning', confirmButtonText: '确认' },
    )
    busy.value = true
    emit('confirm')
  } catch {
    /* 取消 */
  } finally {
    setTimeout(() => (busy.value = false), 500)
  }
}
</script>

<template>
  <el-button :type="danger ? 'danger' : 'default'" :loading="busy || loading" @click="onClick">
    <slot />
  </el-button>
</template>