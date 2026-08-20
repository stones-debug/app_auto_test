<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { listElements, type TestElement } from '@/api/elements'

const props = defineProps<{
  projectId: number
  modelValue?: number | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: number | null): void
}>()

const elements = ref<TestElement[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await listElements(props.projectId, { page: 1, page_size: 200 })
    elements.value = data.items
  } finally {
    loading.value = false
  }
}

function onChange(val: number | null) {
  emit('update:modelValue', val ?? null)
}

onMounted(load)
</script>

<template>
  <el-select
    :model-value="modelValue"
    filterable
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
      :label="`${e.name} (${e.locator_type}: ${e.locator_value})`"
    />
  </el-select>
</template>

<style scoped>
.element-select {
  width: 100%;
}
</style>