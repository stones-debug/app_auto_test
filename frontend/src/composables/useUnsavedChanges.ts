import { ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

// V2 §4.2：编辑页 dirty 状态——路由离开/刷新/关闭提示未保存修改。
export function useUnsavedChanges() {
  const dirty = ref(false)
  const prompt = ref('有未保存的修改，确定离开吗？')

  function markDirty() {
    dirty.value = true
  }

  function markSaved() {
    dirty.value = false
  }

  onBeforeRouteLeave(() => {
    if (dirty.value) {
      return window.confirm(prompt.value)
    }
    return true
  })

  return { dirty, markDirty, markSaved }
}