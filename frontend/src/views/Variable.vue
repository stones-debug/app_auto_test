<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'


import {
  VARIABLE_SCOPES,
  createVariable,
  deleteVariable,
  listVariables,
  updateVariable,
  type Variable,
} from '@/api/suites'
import { useAuthStore } from '@/stores/auth'
import { buildVariableValueUpdate, variableEditSeed } from '@/utils/variableEditing'

const route = useRoute()
const projectId = Number(route.params.projectId)
const auth = useAuthStore()
const isPlatformAdmin = ref(auth.user?.is_admin ?? false)

const scope = ref('project')
const items = ref<Variable[]>([])

// global 仅平台管理员可写（V2 §5.9）
const canWrite = ref(true)

async function load() {
  items.value = await listVariables({
    scope: scope.value,
    project_id: scope.value === 'project' ? projectId : undefined,
  })
}

function onScopeChange() {
  // viewer 只读由后端 403 兜底；此处 global 仅管理员可写
  if (scope.value === 'global') {
    canWrite.value = isPlatformAdmin.value
  } else {
    canWrite.value = true
  }
  load()
}

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref<{ name: string; kind: Variable['kind']; value: string; min: string; max: string; items: string; description: string; is_sensitive: boolean }>({ name: '', kind: 'fixed', value: '', min: '', max: '', items: '', description: '', is_sensitive: false })
const editingVariable = ref<Variable | null>(null)
const sensitiveValueChanged = ref(false)

function openCreate() {
  editingId.value = null
  form.value = { name: '', kind: 'fixed', value: '', min: '', max: '', items: '', description: '', is_sensitive: false }
  editingVariable.value = null
  sensitiveValueChanged.value = false
  dialogVisible.value = true
}

function openEdit(row: Variable) {
  editingId.value = row.id
  editingVariable.value = row
  sensitiveValueChanged.value = false
  form.value = {
    name: row.name, kind: row.kind ?? 'fixed', value: variableEditSeed(row), is_sensitive: row.is_sensitive,
    min: row.spec?.min == null ? '' : String(row.spec.min), max: row.spec?.max == null ? '' : String(row.spec.max),
    items: row.spec?.items?.join('\n') ?? '', description: row.description ?? '',
  }
  dialogVisible.value = true
}

function onValueInput() {
  if (form.value.is_sensitive) sensitiveValueChanged.value = true
}

async function save() {
  if (!form.value.name) {
    ElMessage.warning('请输入变量名')
    return
  }
  let spec: Variable['spec'] = null
  let value = form.value.value
  if (form.value.kind === 'random_integer') {
    const min = Number(form.value.min); const max = Number(form.value.max)
    if (!Number.isSafeInteger(min) || !Number.isSafeInteger(max) || min > max) { ElMessage.warning('请输入有效的随机整数范围'); return }
    value = ''; spec = { min, max }
  } else if (form.value.kind === 'random_choice') {
    const items = form.value.items.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
    const totalBytes = new TextEncoder().encode(items.join('')).byteLength
    if (!items.length || items.length > 100 || items.some((item) => item.length > 1000) || totalBytes > 100000 || new Set(items).size !== items.length) { ElMessage.warning('候选项需为 1-100 个不重复的非空文本，合计不超过 100000 字节'); return }
    value = ''; spec = { items }
  }
  if (editingId.value) {
    const valueUpdate = editingVariable.value
      ? buildVariableValueUpdate(editingVariable.value, value, sensitiveValueChanged.value)
      : { value }
    const updateData: Parameters<typeof updateVariable>[1] = {
      description: form.value.description,
      is_sensitive: form.value.is_sensitive,
      ...(valueUpdate ?? {}),
    }
    // 敏感变量的 spec/value 不回显；未重新输入时只更新明确修改的元数据，避免把掩码或空表单写回。
    if (!editingVariable.value?.is_sensitive || sensitiveValueChanged.value) {
      updateData.kind = form.value.kind
      updateData.spec = spec
    }
    await updateVariable(editingId.value, updateData)
  } else {
    // CR-02：全局变量不携带 project_id
    await createVariable(
      scope.value === 'global'
        ? { scope: 'global', name: form.value.name, kind: form.value.kind, value, spec, description: form.value.description, is_sensitive: form.value.is_sensitive }
        : { scope: scope.value, project_id: projectId, name: form.value.name, kind: form.value.kind, value, spec, description: form.value.description, is_sensitive: form.value.is_sensitive },
    )
  }
  dialogVisible.value = false
  await load()
}

async function remove(row: Variable) {
  await ElMessageBox.confirm(`确认删除变量「${row.name}」？`, '提示', { type: 'warning' })
  await deleteVariable(row.id)
  await load()
}

function scopeLabel(v: string) {
  return VARIABLE_SCOPES.find((s) => s.value === v)?.label ?? v
}

function variableSummary(row: Variable): string {
  if (row.is_sensitive) return '********'
  if (row.kind === 'random_integer') return `随机整数 [${row.spec?.min}, ${row.spec?.max}]`
  if (row.kind === 'random_choice') return `随机列表 ${row.spec?.items?.length ?? 0} 项`
  return row.value || '（空字符串）'
}

onMounted(onScopeChange)
</script>

<template>
  <div>
    <div class="toolbar-card">
      <el-radio-group v-model="scope" @change="onScopeChange">
        <el-radio-button value="project">项目变量</el-radio-button>
        <el-radio-button value="global">全局变量</el-radio-button>
      </el-radio-group>
      <span v-if="scope === 'global'" class="scope-tip v2-aux">全局变量仅平台管理员可管理</span>
      <span class="spacer"></span>
      <el-button v-if="canWrite" type="primary" @click="openCreate">新建变量</el-button>
    </div>

    <el-table :data="items" stripe>
      <el-table-column prop="name" label="变量名" min-width="160" />
      <el-table-column label="值" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ variableSummary(row as Variable) }}</template>
      </el-table-column>
      <el-table-column label="作用域" width="100">
        <template #default="{ row }">
          <el-tag :type="row.scope === 'global' ? 'info' : 'success'" size="small">{{ scopeLabel(row.scope) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <template v-if="canWrite || (row as Variable).scope !== 'global'">
            <el-button size="small" text @click="openEdit(row as Variable)">编辑</el-button>
            <el-button size="small" type="danger" text @click="remove(row as Variable)">删除</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑变量' : '新建变量'" width="480px">
      <el-form label-width="80px">
        <el-form-item label="变量名" required>
          <el-input v-model="form.name" :disabled="!!editingId" placeholder="如 username" />
        </el-form-item>
        <el-form-item label="值">
          <el-select v-model="form.kind" style="width: 100%">
            <el-option label="固定值" value="fixed" /><el-option label="随机整数" value="random_integer" /><el-option label="随机列表" value="random_choice" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.kind === 'fixed'" label="固定值"><el-input v-model="form.value" :type="form.is_sensitive ? 'password' : 'text'" show-password autocomplete="new-password" @input="onValueInput" /></el-form-item>
        <template v-else-if="form.kind === 'random_integer'"><el-form-item label="最小值"><el-input v-model="form.min" /></el-form-item><el-form-item label="最大值"><el-input v-model="form.max" /></el-form-item></template>
        <el-form-item v-else label="候选文本"><el-input v-model="form.items" type="textarea" :rows="5" placeholder="每行一个候选项" /></el-form-item>
        <el-form-item label="敏感值"><el-switch v-model="form.is_sensitive" /><span class="scope-tip">敏感值接口不会回显；编辑时请重新输入</span></el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.scope-tip {
  color: var(--warning);
}
</style>
