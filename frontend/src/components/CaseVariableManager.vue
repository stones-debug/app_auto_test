<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createVariable, deleteVariable, listVariables, updateVariable, type Variable } from '@/api/suites'
import { buildVariableValueUpdate, variableEditSeed } from '@/utils/variableEditing'

const props = defineProps<{ projectId: number; caseId: number; canEdit: boolean }>()

const items = ref<Variable[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const editingVariable = ref<Variable | null>(null)
const sensitiveValueChanged = ref(false)
const form = ref({ name: '', value: '', description: '', is_sensitive: false })

async function load() {
  loading.value = true
  try {
    items.value = await listVariables({ scope: 'case', case_id: props.caseId })
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.value = { name: '', value: '', description: '', is_sensitive: false }
  editingId.value = null
  editingVariable.value = null
  sensitiveValueChanged.value = false
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(variable: Variable) {
  editingId.value = variable.id
  editingVariable.value = variable
  sensitiveValueChanged.value = false
  form.value = {
    name: variable.name,
    value: variableEditSeed(variable),
    description: variable.description ?? '',
    is_sensitive: variable.is_sensitive,
  }
  dialogVisible.value = true
}

function onValueInput() {
  if (form.value.is_sensitive) sensitiveValueChanged.value = true
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入变量名')
    return
  }
  saving.value = true
  try {
    if (editingId.value !== null && editingVariable.value) {
      const valueUpdate = buildVariableValueUpdate(editingVariable.value, form.value.value, sensitiveValueChanged.value)
      await updateVariable(editingId.value, {
        ...(valueUpdate ?? {}),
        description: form.value.description.trim() || null,
        is_sensitive: form.value.is_sensitive,
      })
    } else {
      await createVariable({
        scope: 'case',
        project_id: props.projectId,
        case_id: props.caseId,
        name: form.value.name.trim(),
        value: form.value.value,
        description: form.value.description.trim() || null,
        is_sensitive: form.value.is_sensitive,
      })
    }
    dialogVisible.value = false
    ElMessage.success('用例变量已保存')
    await load()
  } finally {
    saving.value = false
  }
}

async function remove(variable: Variable) {
  try {
    await ElMessageBox.confirm(`确认删除变量「${variable.name}」？`, '删除变量', { type: 'warning' })
  } catch {
    return
  }
  await deleteVariable(variable.id)
  ElMessage.success('用例变量已删除')
  await load()
}

function displayValue(variable: Variable): string {
  return variable.is_sensitive ? '********' : (variable.value || '（空字符串）')
}

onMounted(() => void load())
</script>

<template>
  <section class="content-card case-variable-card" v-loading="loading">
    <div class="section-head">
      <div>
        <div class="section-title">用例变量</div>
        <div class="section-hint">正式 case-scope 变量会获得稳定身份，并参与项目变量解析。</div>
      </div>
      <el-button v-if="props.canEdit" type="primary" size="small" @click="openCreate">新增变量</el-button>
    </div>
    <el-empty v-if="!loading && items.length === 0" :image-size="48" description="暂无用例变量" />
    <div v-else class="variable-list">
      <div v-for="variable in items" :key="variable.id" class="variable-row">
        <span class="variable-name">{{ variable.name }}</span>
        <el-tag v-if="variable.is_sensitive" size="small" type="warning">敏感</el-tag>
        <span class="variable-value">{{ displayValue(variable) }}</span>
        <span class="variable-actions">
          <el-button v-if="props.canEdit" size="small" text @click="openEdit(variable)">编辑</el-button>
          <el-button v-if="props.canEdit" size="small" type="danger" text @click="remove(variable)">删除</el-button>
        </span>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editingId === null ? '新增用例变量' : '编辑用例变量'" width="480px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="变量名" required>
          <el-input v-model="form.name" :disabled="editingId !== null" placeholder="如 username" />
        </el-form-item>
        <el-form-item label="值">
          <el-input v-model="form.value" :type="form.is_sensitive ? 'password' : 'text'" show-password autocomplete="new-password"
            :placeholder="form.is_sensitive ? '敏感值不会回显，请重新输入' : '可保存为空字符串'" @input="onValueInput" />
        </el-form-item>
        <el-form-item label="敏感值"><el-switch v-model="form.is_sensitive" /><span class="section-hint">接口不会回显敏感值</span></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.case-variable-card { margin-bottom: 16px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.section-title { font-size: 15px; font-weight: 600; color: var(--text); }
.section-hint { color: var(--text-2); font-size: 12px; line-height: 1.6; }
.variable-list { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
.variable-row { display: flex; align-items: center; gap: 10px; min-height: 34px; }
.variable-name { width: 180px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.variable-value { flex: 1; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.variable-actions { display: flex; gap: 2px; }
</style>
