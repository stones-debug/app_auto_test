<script setup lang="ts">
import { computed } from 'vue'

import { formatParameters, hasParameters } from '@/utils/parameters'

const props = defineProps<{ parameters: Record<string, unknown> }>()
const content = computed(() => formatParameters(props.parameters, true))
</script>

<template>
  <pre v-if="hasParameters(parameters)" class="parameters-content">{{ content }}</pre>
  <span v-else class="v2-aux">无执行参数</span>
</template>

<style scoped>
.parameters-content {
  margin: 0;
  padding: 8px 12px;
  overflow-x: auto;
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
