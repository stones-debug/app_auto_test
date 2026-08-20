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
const pageSize = ref(12)
const visibility = ref('all')
const keyword = ref('')

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', description: '', visibility: 'private' })

async function load() {
  loading.value = true
  try {
    const data = await listProjects({
      page: page.value,
      page_size: pageSize.value,
      visibility: visibility.value,
    })
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

function roleLabel(r: string | null | undefined) {
  return ({ owner: '拥有者', admin: '管理员', member: '成员', viewer: '访客' } as Record<string, string>)[r ?? ''] ?? r ?? ''
}

function fmtDate(s: string) {
  return s ? new Date(s).toLocaleDateString() : ''
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar-card">
      <el-input v-model="keyword" placeholder="按项目名称搜索" clearable class="search" @keyup.enter="page = 1; load()" />
      <el-radio-group v-model="visibility" @change="page = 1; load()">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="mine">我创建的</el-radio-button>
        <el-radio-button value="public">公开</el-radio-button>
      </el-radio-group>
      <span class="spacer"></span>
      <el-button type="primary" @click="openCreate">新建项目</el-button>
    </div>

    <div v-loading="loading">
      <div v-if="projects.length" class="card-grid">
        <div v-for="p in projects" :key="p.id" class="project-card" @dblclick="openProject(p)">
          <div class="card-head">
            <span class="name">{{ p.name }}</span>
            <div class="tags">
              <el-tag :type="p.visibility === 'public' ? 'success' : 'info'" size="small" effect="plain">
                {{ visibilityLabel(p.visibility) }}
              </el-tag>
              <el-tag size="small" type="warning" effect="plain">{{ roleLabel(p.role) }}</el-tag>
            </div>
          </div>
          <p class="desc">{{ p.description || '暂无描述' }}</p>
          <div class="stats">
            <span>用例 <b>{{ p.case_count }}</b></span>
            <span>元素 <b>{{ p.element_count }}</b></span>
            <span>套件 <b>{{ p.suite_count }}</b></span>
          </div>
          <div class="card-foot">
            <span class="time">创建于 {{ fmtDate(p.created_at) }}</span>
            <span class="actions">
              <el-button size="small" type="primary" text @click="openProject(p)">进入</el-button>
              <el-button size="small" text @click="openEdit(p)">编辑</el-button>
              <el-button size="small" type="danger" text @click="remove(p)">删除</el-button>
            </span>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">暂无项目，点击右上角「新建项目」开始</div>
    </div>

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
.search {
  width: 220px;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.project-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  cursor: pointer;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.project-card:hover {
  box-shadow: 0 4px 16px rgba(30, 41, 59, 0.08);
  border-color: var(--primary);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tags {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.desc {
  font-size: 13px;
  color: var(--text-2);
  min-height: 36px;
  margin-bottom: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.stats {
  display: flex;
  gap: 18px;
  font-size: 13px;
  color: var(--text-2);
  margin-bottom: 14px;
}
.stats b {
  color: var(--primary);
  font-size: 15px;
}
.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}
.time {
  font-size: 12px;
  color: var(--text-2);
}
</style>
