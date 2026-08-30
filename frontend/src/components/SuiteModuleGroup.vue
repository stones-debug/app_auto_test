<script setup lang="ts">
import type { CaseGroup, CaseStatusMeta } from '@/composables/useSuiteCases'

defineProps<{
  group: CaseGroup
  selectedIds: ReadonlySet<number>
  collapsed: boolean
  selection: { checked: boolean; indeterminate: boolean }
  caseStatusMeta: (status: string) => CaseStatusMeta
}>()

const emit = defineEmits<{
  toggle: [id: number]
  'toggle-group': [selected: boolean]
  collapse: []
}>()
</script>

<template>
  <div class="case-group">
    <div class="case-group-head">
      <button
        type="button"
        class="case-group-toggle"
        :aria-expanded="!collapsed"
        @click="emit('collapse')"
      >
        <span class="case-group-chevron" :class="{ collapsed }" aria-hidden="true">⌄</span>
        <span>{{ group.name }}</span>
      </button>
      <span class="case-group-actions">
        <span class="case-group-count">{{ group.cases.length }} 个</span>
        <el-checkbox
          :model-value="selection.checked"
          :indeterminate="selection.indeterminate"
          @click.stop
          @change="emit('toggle-group', Boolean($event))"
        >
          全选
        </el-checkbox>
      </span>
    </div>
    <template v-if="!collapsed">
      <div
        v-for="item in group.cases"
        :key="item.id"
        class="case-pick-row"
        :class="{ selected: selectedIds.has(item.id) }"
        role="checkbox"
        :aria-checked="selectedIds.has(item.id)"
        tabindex="0"
        @click="emit('toggle', item.id)"
        @keyup.enter="emit('toggle', item.id)"
      >
        <span class="pick-check" :class="{ on: selectedIds.has(item.id) }">
          {{ selectedIds.has(item.id) ? '✓' : '' }}
        </span>
        <span class="case-pick-name" :title="item.name">{{ item.name }}</span>
        <el-tag v-if="item.status !== 'active'" :type="caseStatusMeta(item.status).type" size="small" effect="light">
          {{ caseStatusMeta(item.status).label }}
        </el-tag>
      </div>
    </template>
  </div>
</template>

<style scoped>
.case-group + .case-group { border-top: 1px solid var(--border); }
.case-group-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f8fafc;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.case-group-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 0;
  color: inherit;
  font: inherit;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.case-group-toggle:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
  border-radius: 3px;
}
.case-group-chevron {
  display: inline-block;
  color: var(--text-2);
  line-height: 1;
  transition: transform 0.12s;
}
.case-group-chevron.collapsed { transform: rotate(-90deg); }
.case-group-count { color: var(--text-2); font-weight: 400; font-size: 12px; }
.case-group-actions { display: inline-flex; align-items: center; gap: 10px; font-weight: 400; }
.case-group-actions :deep(.el-checkbox) { height: auto; margin-right: 0; }
.case-pick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  cursor: pointer;
  border-top: 1px solid var(--border);
}
.case-pick-row:hover, .case-pick-row.selected { background: var(--primary-light); }
.pick-check {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: #fff;
  font-size: 12px;
}
.pick-check.on { background: var(--primary); border-color: var(--primary); }
.case-pick-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
