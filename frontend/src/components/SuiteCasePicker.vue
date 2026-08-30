<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'

import type { CaseGroup, CaseStatusMeta } from '@/composables/useSuiteCases'
import SuiteModuleGroup from '@/components/SuiteModuleGroup.vue'

defineProps<{
  modelValue: boolean
  groups: CaseGroup[]
  allCasesCount: number
  keyword: string
  selectedIds: ReadonlySet<number>
  loading: boolean
  adding: boolean
  caseStatusMeta: (status: string) => CaseStatusMeta
  groupSelectionState: (group: CaseGroup) => { checked: boolean; indeterminate: boolean }
  isGroupCollapsed: (group: CaseGroup) => boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  'update:keyword': [keyword: string]
  toggle: [id: number]
  'toggle-group': [group: CaseGroup, selected: boolean]
  collapse: [group: CaseGroup]
  add: []
}>()
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="添加用例"
    width="560px"
    append-to-body
    class="add-case-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-input
      :model-value="keyword"
      placeholder="搜索用例名称"
      clearable
      class="add-case-search"
      @update:model-value="emit('update:keyword', $event)"
    >
      <template #prefix><el-icon><Search /></el-icon></template>
    </el-input>
    <div class="add-case-hint v2-aux">从用例库挑选用例，可多选批量添加。</div>
    <div v-loading="loading" class="add-case-body">
      <template v-if="groups.length">
        <SuiteModuleGroup
          v-for="group in groups"
          :key="group.name"
          :group="group"
          :selected-ids="selectedIds"
          :collapsed="isGroupCollapsed(group)"
          :selection="groupSelectionState(group)"
          :case-status-meta="caseStatusMeta"
          @toggle="emit('toggle', $event)"
          @toggle-group="emit('toggle-group', group, $event)"
          @collapse="emit('collapse', group)"
        />
      </template>
      <div v-else class="add-case-empty v2-aux">
        {{ loading ? '正在加载用例…' : allCasesCount ? '没有匹配的用例' : '该套件已加入全部可用用例' }}
      </div>
    </div>
    <template #footer>
      <span class="add-case-count">{{ selectedIds.size ? `已选 ${selectedIds.size} 个` : '' }}</span>
      <span class="add-case-footer-actions">
        <el-button @click="emit('update:modelValue', false)">取消</el-button>
        <el-button type="primary" :loading="adding" :disabled="selectedIds.size === 0" @click="emit('add')">
          添加({{ selectedIds.size }})
        </el-button>
      </span>
    </template>
  </el-dialog>
</template>

<style scoped>
.add-case-search { width: 100%; }
.add-case-hint { margin: 10px 0 12px; }
.add-case-body { max-height: 360px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; }
.add-case-empty { padding: 28px 12px; text-align: center; }
.add-case-count { color: var(--text-2); }
.add-case-footer-actions { display: inline-flex; gap: 8px; margin-left: auto; }
</style>
