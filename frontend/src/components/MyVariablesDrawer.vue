<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { listMyVariables, patchMyVariables, type MyVariableItem, type MyVariableScope } from '@/api/appProfiles'
import { apiErrorDetail } from '@/utils/request'
import { createUuid } from '@/utils/uuid'
import { buildMyVariableValueUpdate, buildRestoreVariableUpdate, myVariableEditSeed, shouldApplyVariableResponse } from '@/utils/variableEditing'

const props = defineProps<{ profileId: number; canEdit: boolean }>()
const visible = defineModel<boolean>('modelValue')

const keyword = ref('')
const scope = ref<'all' | MyVariableScope>('all')
const overriddenOnly = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const items = ref<MyVariableItem[]>([])
const loading = ref(false)
const error = ref('')
const editingId = ref<number | null>(null)
const drafts = ref<Record<number, string>>({})
const savingId = ref<number | null>(null)
let requestSequence = 0

function displayValue(item: MyVariableItem): string {
  if (item.is_sensitive) return item.overridden ? '已配置' : '未配置'
  if (item.display_value === '') return '（空）'
  return item.display_value || '未定义'
}

function publicValueText(item: MyVariableItem): string {
  if (item.is_sensitive) return item.display_value === '********' ? '已配置' : '未配置'
  if (item.public_value === '') return '（空）'
  return item.public_value || '未定义'
}

function userValueText(item: MyVariableItem): string {
  if (item.is_sensitive) return item.overridden ? '已配置' : '未配置'
  if (item.user_value === '') return '（空）'
  return item.user_value ?? '未覆盖'
}

function ownerText(item: MyVariableItem): string {
  if (item.scope === 'project') return `项目变量${item.project_id != null ? ` #${item.project_id}` : ''}`
  if (item.scope === 'suite') return item.suite_name ?? `套件 #${item.suite_id ?? '-'}`
  return item.case_name ?? `用例 #${item.case_id ?? '-'}`
}

function variableRow(row: unknown): MyVariableItem {
  return row as MyVariableItem
}

function variableToken(name: string): string {
  return '${' + name + '}'
}

function editSeed(item: MyVariableItem): string {
  // 敏感值永远不从响应回填；用户必须重新输入才会写入。
  return myVariableEditSeed(item.is_sensitive, item.user_value, item.public_value)
}

function beginEdit(item: MyVariableItem) {
  if (!props.canEdit) return
  editingId.value = item.variable_id
  drafts.value = { ...drafts.value, [item.variable_id]: editSeed(item) }
}

function cancelEdit() {
  editingId.value = null
}

async function load() {
  const sequence = ++requestSequence
  loading.value = true
  error.value = ''
  try {
    const result = await listMyVariables(props.profileId, {
      keyword: keyword.value || undefined,
      scope: scope.value === 'all' ? undefined : scope.value,
      overridden_only: overriddenOnly.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    if (!shouldApplyVariableResponse(sequence, requestSequence)) return
    items.value = result.items
    total.value = result.total
  } catch (cause) {
    if (!shouldApplyVariableResponse(sequence, requestSequence)) return
    items.value = []
    total.value = 0
    error.value = apiErrorDetail(cause)?.message ?? (cause instanceof Error ? cause.message : '加载变量失败')
  } finally {
    if (shouldApplyVariableResponse(sequence, requestSequence)) loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  void load()
}

function onPageChange(nextPage: number) {
  page.value = nextPage
  void load()
}

function onSizeChange(nextSize: number) {
  pageSize.value = nextSize
  page.value = 1
  void load()
}

async function save(item: MyVariableItem) {
  if (!props.canEdit || savingId.value != null) return
  const value = drafts.value[item.variable_id] ?? ''
  const currentValue = item.user_value ?? item.public_value ?? ''
  if (!item.is_sensitive && value === currentValue) {
    cancelEdit()
    return
  }
  savingId.value = item.variable_id
  try {
    await patchMyVariables(props.profileId, {
      request_id: createUuid(),
      updates: [{ variable_id: item.variable_id, ...buildMyVariableValueUpdate(value) }],
    })
    cancelEdit()
    await load()
    ElMessage.success('我的变量已更新')
  } catch (cause) {
    ElMessage.error(apiErrorDetail(cause)?.message ?? (cause instanceof Error ? cause.message : '保存变量失败'))
  } finally {
    savingId.value = null
  }
}

async function restore(item: MyVariableItem) {
  if (!props.canEdit || savingId.value != null || !item.overridden) return
  savingId.value = item.variable_id
  try {
    await patchMyVariables(props.profileId, {
      request_id: createUuid(),
      updates: [{ variable_id: item.variable_id, ...buildRestoreVariableUpdate() }],
    })
    cancelEdit()
    await load()
    ElMessage.success('已恢复公共值')
  } catch (cause) {
    ElMessage.error(apiErrorDetail(cause)?.message ?? (cause instanceof Error ? cause.message : '恢复变量失败'))
  } finally {
    savingId.value = null
  }
}

watch(() => props.profileId, () => {
  page.value = 1
  if (visible.value) void load()
})
watch(visible, (open) => {
  if (open) {
    page.value = 1
    void load()
  } else {
    editingId.value = null
  }
})

defineExpose({ load })
</script>

<template>
  <el-drawer v-model="visible" title="我的变量配置" size="760px">
    <el-alert type="info" :closable="false" show-icon>
      仅当前账号在此 APP 档案中生效。档案能力变量优先级更高，不能被我的变量突破；其他账号看不到这里的个人值。
    </el-alert>
    <div class="variable-toolbar">
      <el-input v-model="keyword" clearable placeholder="搜索变量名" style="width: 190px" @keyup.enter="applyFilter" @change="applyFilter" />
      <el-select v-model="scope" style="width: 130px" @change="applyFilter">
        <el-option label="全部作用域" value="all" />
        <el-option label="项目" value="project" />
        <el-option label="套件" value="suite" />
        <el-option label="用例" value="case" />
      </el-select>
      <el-checkbox v-model="overriddenOnly" @change="applyFilter">只看已覆盖</el-checkbox>
      <el-button size="small" @click="load">刷新</el-button>
    </div>
    <el-alert v-if="error" type="error" :closable="false" class="variable-error">{{ error }}</el-alert>
    <el-table v-loading="loading" :data="items" row-key="variable_id" size="small">
      <el-table-column label="变量" min-width="150">
        <template #default="{ row }"><code>{{ variableToken(variableRow(row).name) }}</code></template>
      </el-table-column>
      <el-table-column label="作用域 / 所属" min-width="170">
        <template #default="{ row }"><span>{{ variableRow(row).scope }}</span><small class="owner">{{ ownerText(variableRow(row)) }}</small></template>
      </el-table-column>
      <el-table-column label="公共值" min-width="150">
        <template #default="{ row }"><span>{{ publicValueText(variableRow(row)) }}</span></template>
      </el-table-column>
      <el-table-column label="我的值" min-width="150">
        <template #default="{ row }"><span>{{ userValueText(variableRow(row)) }}</span></template>
      </el-table-column>
      <el-table-column label="当前展示值" min-width="180">
        <template #default="{ row }">
          <template v-if="editingId === row.variable_id">
            <el-input v-model="drafts[row.variable_id]" :type="variableRow(row).is_sensitive ? 'password' : 'text'" show-password
              autocomplete="new-password" autofocus :disabled="savingId === row.variable_id"
              :placeholder="variableRow(row).is_sensitive ? '重新输入敏感值' : '可保存为空字符串'"
              @keyup.enter="save(variableRow(row))" @keyup.esc="cancelEdit" />
          </template>
          <span v-else :class="{ sensitive: variableRow(row).is_sensitive }">{{ displayValue(variableRow(row)) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="个人覆盖" width="100">
        <template #default="{ row }">{{ row.overridden ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="引用" width="70"><template #default="{ row }">{{ row.reference_count }} 处</template></el-table-column>
      <el-table-column label="操作" width="150" align="right">
        <template #default="{ row }">
          <template v-if="editingId === row.variable_id">
            <el-button size="small" type="primary" :loading="savingId === row.variable_id" @click="save(variableRow(row))">保存</el-button>
            <el-button size="small" :disabled="savingId === row.variable_id" @click="cancelEdit">取消</el-button>
          </template>
          <template v-else>
            <el-button v-if="props.canEdit" size="small" text :disabled="savingId != null" @click="beginEdit(variableRow(row))">编辑</el-button>
            <el-button v-if="props.canEdit && row.overridden" size="small" text type="danger" :disabled="savingId != null" @click="restore(variableRow(row))">恢复</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="total > 0" class="variable-pagination" background layout="total, sizes, prev, pager, next" :total="total"
      v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" @current-change="onPageChange" @size-change="onSizeChange" />
  </el-drawer>
</template>

<style scoped>
.variable-toolbar { display: flex; align-items: center; gap: 8px; margin: 16px 0 12px; flex-wrap: wrap; }
.variable-error { margin-bottom: 12px; }
.owner { display: block; color: var(--el-text-color-secondary); margin-top: 2px; }
.sensitive { letter-spacing: 0.08em; }
.variable-pagination { justify-content: flex-end; margin-top: 14px; }
</style>
