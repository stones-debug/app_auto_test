<script setup lang="ts">
import { computed } from 'vue'

import {
  handleVariableAreaClick,
  variablePreviewChips,
  variableStatusMeta,
  variableToken,
  type VariableChip,
} from '@/utils/caseVariables'

const props = withDefaults(
  defineProps<{
    variables?: VariableChip[]
    /** 变量总数（可能大于行内展示的 2 项） */
    total?: number
    readonly?: boolean
  }>(),
  { variables: () => [], total: 0, readonly: false },
)

const emit = defineEmits<{ open: [] }>()

const chips = computed(() => variablePreviewChips(props.variables, props.total))

function onOpen(event: Event) {
  // 点击变量区域必须阻止拖拽、排序编辑、双击打开用例等父级事件
  handleVariableAreaClick(event, props.readonly, () => emit('open'))
}

function stopParentEvent(event: Event) {
  event.stopPropagation()
}
</script>

<template>
  <span
    class="case-variable-summary"
    draggable="false"
    @click="onOpen"
    @dblclick="stopParentEvent"
    @mousedown="stopParentEvent"
  >
    <template v-if="chips.visible.length">
      <span
        v-for="chip in chips.visible"
        :key="chip.name"
        class="variable-chip"
        :class="`tone-${variableStatusMeta(chip.status).tone}`"
        :title="`${variableToken(chip.name)}：${chip.display_value || '（空）'}（${variableStatusMeta(chip.status).label}）`"
      >
        <span class="chip-name">{{ variableToken(chip.name) }}</span>
        <span class="chip-value">{{ chip.display_value || '空' }}</span>
      </span>
      <span v-if="chips.hidden > 0" class="variable-chip more-chip" :title="`还有 ${chips.hidden} 个变量`">
        +{{ chips.hidden }} 更多 ▾
      </span>
    </template>
    <span v-else class="variable-empty">无参数变量</span>
  </span>
</template>

<style scoped>
.case-variable-summary {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
  cursor: pointer;
}

.variable-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 200px;
  padding: 1px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-name {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.chip-value {
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0.85;
}

/* 蓝色：当前层已覆盖；灰色：继承；橙色：未定义；紫色：随机；多个值：混合态 */
.tone-overridden {
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.12);
  border-color: rgba(37, 99, 235, 0.35);
}

.tone-inherited {
  color: var(--text-2, #64748b);
  background: rgba(100, 116, 139, 0.1);
  border-color: rgba(100, 116, 139, 0.28);
}

.tone-undefined {
  color: #c2410c;
  background: rgba(249, 115, 22, 0.12);
  border-color: rgba(249, 115, 22, 0.35);
}

.tone-random {
  color: #7c3aed;
  background: rgba(139, 92, 246, 0.14);
  border-color: rgba(139, 92, 246, 0.38);
}

.tone-mixed {
  color: #b45309;
  background: rgba(245, 158, 11, 0.14);
  border-color: rgba(245, 158, 11, 0.4);
}

.more-chip {
  color: var(--text-2, #64748b);
  background: transparent;
  border-color: var(--border, rgba(100, 116, 139, 0.3));
}

.more-chip:hover {
  color: var(--primary, #4f46e5);
  border-color: var(--primary, #4f46e5);
}

.variable-empty {
  color: var(--text-2, #94a3b8);
  font-size: 12px;
}
</style>
