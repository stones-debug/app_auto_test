<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createProject,
  deleteProject,
  listProjects,
  updateProject,
  type Project,
} from '@/api/projects'

const router = useRouter()
const loading = ref(false)
const projects = ref<Project[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const visibility = ref('all')

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', description: '', visibility: 'private' })

async function load() {
  loading.value = true
  try {
    const data = await listProjects({ page: page.value, page_size: pageSize.value, visibility: visibility.value })
    projects.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.value = { name: '', description: '', visibility: 'private' }
  dialogVisible.value = true
}

function openEdit(row: Project) {
  editingId.value = row.id
  form.value = { name: row.name, description: row.description ?? '', visibility: row.visibility }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.name) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (editingId.value) {
    await updateProject(editingId.value, form.value)
    ElMessage.success('已更新')
  } else {
    await createProject(form.value)
    ElMessage.success('已创建')
  }
  dialogVisible.value = false
  await load()
}

async function remove(row: Project) {
  await ElMessageBox.confirm(`确认删除项目「${row.name}」？`, '提示', { type: 'warning' })
  await deleteProject(row.id)
  ElMessage.success('已删除')
  await load()
}

function openProject(project: Project) {
  router.push(`/projects/${project.id}/cases`)
}

function visibilityLabel(v: string) {
  return v === 'public' ? '公开' : '私有'
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <el-radio-group v-model="visibility" @change="load">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="mine">我创建的</el-radio-button>
        <el-radio-button value="public">公开</el-radio-button>
      </el-radio-group>
      <el-button type="primary" @click="openCreate">新建项目</el-button>
    </div>

    <el-table v-loading="loading" :data="projects" @row-dblclick="openProject">
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
      <el-table-column label="可见性" width="90">
        <template #default="{ row }">
          <el-tag :type="row.visibility === 'public' ? 'success' : 'info'" size="small">
            {{ visibilityLabel(row.visibility) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="我的角色" width="100">
        <template #default="{ row }">
          <el-tag size="small">{{ row.role }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" text @click="openProject(row)">进入</el-button>
          <el-button size="small" text @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" text @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      class="pager"
      @change="load"
    />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑项目' : '新建项目'" width="480px">
      <el-form label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="项目名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="可见性">
          <el-radio-group v-model="form.visibility">
            <el-radio value="private">私有</el-radio>
            <el-radio value="public">公开</el-radio>
          </el-radio-group>
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
.toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>