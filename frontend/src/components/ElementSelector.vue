<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { getElement, listElements, type TestElement } from '@/api/elements'

const props = defineProps<{
  modelValue?: number | null
  projectId?: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: number | null): void
}>()

const elements = ref<TestElement[]>([])
const loading = ref(false)
let loadVersion = 0

async function load(keyword = '') {
  const version = ++loadVersion
  loading.value = true
  try {
    const data = await listElements({
      page: 1,
      page_size: 200,
      ...(keyword ? { keyword } : {}),
      ...(props.projectId === undefined ? {} : { project_id: props.projectId }),
    })
    // 远程搜索可能连续触发请求，只接受最后一次结果，避免旧请求覆盖新结果。
    if (version !== loadVersion) return
    elements.value = data.items
    await ensureSelectedElement(version)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

async function ensureSelectedElement(version = loadVersion) {
  const elementId = props.modelValue
  if (elementId == null || elements.value.some((element) => element.id === elementId)) return
  try {
    const selected = await getElement(elementId)
    if (version !== loadVersion) return
    if (props.projectId === undefined || selected.project_id === props.projectId) {
      elements.value = [...elements.value, selected]
    }
  } catch {
    // 当前元素已删除或无权访问时保留空选项，避免阻塞用例编辑。
  }
}

function searchElements(query: string) {
  void load(query.trim())
}

function onChange(val: number | null) {
  emit('update:modelValue', val ?? null)
}

onMounted(load)
watch(() => props.modelValue, () => {
  void ensureSelectedElement()
})
</script>

<template>
  <el-select
    :model-value="modelValue"
    filterable
    remote
    :remote-method="searchElements"
    clearable
    placeholder="选择元素（可清空）"
    :loading="loading"
    class="element-select"
    @change="onChange"
  >
    <el-option
      v-for="e in elements"
      :key="e.id"
      :value="e.id"
      :label="`[${e.project_name ?? '?'}] ${e.name} (${e.locator_type === 'smart' ? '智能定位' : `${e.locator_type}: ${e.locator_value}`})`"
    />
  </el-select>
</template>

<style scoped>
.element-select {
  width: 100%;
}
</style>
