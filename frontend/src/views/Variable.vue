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

const route = useRoute()
const projectId = Number(route.params.projectId)

const scope = ref('project')
const items = ref<Variable[]>([])

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', value: '', description: '' })

async function load() {
  items.value = await listVariables({
    scope: scope.value,
    project_id: scope.value === 'project' ? projectId : undefined,
  })
}

function openCreate() {
  editingId.value = null
  form.value = { name: '', value: '', description: '' }
  dialogVisible.value = true
}

function openEdit(row: Variable) {
  editingId.value = row.id
  form.value = { name: row.name, value: row.value, description: row.description ?? '' }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.name) {
    ElMessage.warning('请输入变量名')
    return
  }
  if (editingId.value) {
    await updateVariable(editingId.value, { value: form.value.value, description: form.value.description })
  } else {
    // CR-02：全局变量不携带 project_id
    await createVariable(
      scope.value === 'global'
        ? { scope: 'global', ...form.value }
        : { scope: scope.value, project_id: projectId, ...form.value },
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

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar-card">
      <el-radio-group v-model="scope" @change="load">
        <el-radio-button value="project">项目变量</el-radio-button>
        <el-radio-button value="global">全局变量</el-radio-button>
      </el-radio-group>
      <span class="spacer"></span>
      <el-button type="primary" @click="openCreate">新建变量</el-button>
    </div>

    <el-table :data="items" stripe>
      <el-table-column prop="name" label="变量名" min-width="160" />
      <el-table-column prop="value" label="值" min-width="200" show-overflow-tooltip />
      <el-table-column label="作用域" width="100">
        <template #default="{ row }">
          <el-tag :type="row.scope === 'global' ? 'info' : 'success'" size="small">{{ scopeLabel(row.scope) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text @click="openEdit(row as Variable)">编辑</el-button>
          <el-button size="small" type="danger" text @click="remove(row as Variable)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑变量' : '新建变量'" width="480px">
      <el-form label-width="80px">
        <el-form-item label="变量名" required>
          <el-input v-model="form.name" :disabled="!!editingId" placeholder="如 username" />
        </el-form-item>
        <el-form-item label="值">
          <el-input v-model="form.value" />
        </el-form-item>
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
