<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  addMember,
  deleteProject,
  listMembers,
  memberCandidates,
  removeMember,
  updateMember,
  updateProject,
  type ProjectMember,
  type UserCandidate,
} from '@/api/projects'
import { usePermission } from '@/composables/usePermission'
import { useProjectContextStore } from '@/stores/projectContext'

const route = useRoute()
const router = useRouter()
const ctx = useProjectContextStore()
const { canEditProject, canManageMembers, canDeleteProject } = usePermission()

const projectId = computed(() => Number(route.params.projectId))
const activeTab = ref('info')
const saving = ref(false)

// 基本信息
const form = ref({ name: '', description: '', visibility: 'private' })

// 成员
const members = ref<ProjectMember[]>([])
const memberLoading = ref(false)
const addOpen = ref(false)
const keyword = ref('')
const candidates = ref<UserCandidate[]>([])
const searchLoading = ref(false)
const selectedUser = ref<UserCandidate | null>(null)
const newRole = ref('viewer')

const ROLE_LABEL: Record<string, string> = { owner: '拥有者', admin: '管理员', member: '成员', viewer: '访客' }

async function loadInfo() {
  await ctx.load(projectId.value, { force: true })
  if (ctx.project) {
    form.value = {
      name: ctx.project.name,
      description: ctx.project.description ?? '',
      visibility: ctx.project.visibility,
    }
  }
}

async function saveInfo() {
  saving.value = true
  try {
    await updateProject(projectId.value, form.value)
    ElMessage.success('已保存')
    await loadInfo()
  } finally {
    saving.value = false
  }
}

async function loadMembers() {
  memberLoading.value = true
  try {
    members.value = await listMembers(projectId.value)
  } finally {
    memberLoading.value = false
  }
}

async function searchCandidates(query: string) {
  keyword.value = query
  if (!keyword.value.trim()) {
    candidates.value = []
    return
  }
  searchLoading.value = true
  try {
    candidates.value = await memberCandidates(projectId.value, keyword.value.trim())
  } finally {
    searchLoading.value = false
  }
}

async function submitAdd() {
  if (!selectedUser.value) return
  await addMember(projectId.value, { user_id: selectedUser.value.id, role: newRole.value })
  ElMessage.success('已添加成员')
  addOpen.value = false
  selectedUser.value = null
  keyword.value = ''
  newRole.value = 'viewer'
  await loadMembers()
}

async function changeRole(m: ProjectMember) {
  try {
    await updateMember(projectId.value, m.user_id, m.role)
    ElMessage.success('已更新角色')
  } catch {
    await loadMembers()
  }
}

async function remove(m: ProjectMember) {
  await ElMessageBox.confirm(`确认移除成员「${m.username}」？`, '提示', { type: 'warning' })
  await removeMember(projectId.value, m.user_id)
  ElMessage.success('已移除')
  await loadMembers()
}

async function removeProject() {
  const value = await ElMessageBox.prompt(
    `删除项目「${form.value.name}」将永久移除其全部内容，请输入项目名确认：`,
    '删除项目',
    { type: 'warning', inputPlaceholder: form.value.name, inputValidator: (v: string) => v === form.value.name || '请输入正确的项目名' },
  ).then((r) => r.value as string)
  if (value !== form.value.name) return
  await deleteProject(projectId.value)
  ElMessage.success('已删除项目')
  router.push('/projects')
}

onMounted(() => {
  loadInfo()
  if (canManageMembers) loadMembers()
})
</script>

<template>
  <div>
    <div class="settings-layout">
      <el-menu :default-active="activeTab" class="settings-menu" @select="(k: string) => (activeTab = k)">
        <el-menu-item index="info">基本信息</el-menu-item>
        <el-menu-item index="members">成员与权限</el-menu-item>
        <el-menu-item index="danger">危险操作</el-menu-item>
      </el-menu>

      <div class="settings-body">
        <!-- 基本信息 -->
        <div v-if="activeTab === 'info'">
          <el-form label-width="90px" class="form-wrap">
            <el-form-item label="名称" required>
              <el-input v-model="form.name" :disabled="!canEditProject" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="form.description" type="textarea" :rows="3" :disabled="!canEditProject" />
            </el-form-item>
            <el-form-item label="可见性">
              <el-radio-group v-model="form.visibility" :disabled="!canEditProject">
                <el-radio value="private">私有</el-radio>
                <el-radio value="public">公开（全部登录用户可见）</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item>
              <el-button v-if="canEditProject" type="primary" :loading="saving" @click="saveInfo">保存</el-button>
              <span v-else class="v2-aux">仅 owner/admin 可编辑</span>
            </el-form-item>
          </el-form>
        </div>

        <!-- 成员与权限 -->
        <div v-if="activeTab === 'members'">
          <div class="member-head">
            <div class="v2-card-title">项目成员</div>
            <el-button v-if="canManageMembers" type="primary" size="small" @click="addOpen = true">添加成员</el-button>
          </div>
          <el-table v-loading="memberLoading" :data="members" size="small">
            <el-table-column prop="username" label="用户名" min-width="140" />
            <el-table-column label="角色" width="180">
              <template #default="{ row }">
                <template v-if="(row as ProjectMember).membership_id === null">
                  <el-tag type="warning" size="small">{{ ROLE_LABEL[(row as ProjectMember).role] ?? (row as ProjectMember).role }}</el-tag>
                </template>
                <el-select v-else v-model="(row as ProjectMember).role" size="small" :disabled="!canManageMembers" @change="changeRole(row as ProjectMember)">
                  <el-option v-for="(label, value) in ROLE_LABEL" :key="value" :label="label" :value="value" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="right">
              <template #default="{ row }">
                <el-button v-if="canManageMembers && (row as ProjectMember).membership_id !== null" size="small" type="danger" text @click="remove(row as ProjectMember)">
                  移除
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-dialog v-model="addOpen" title="添加成员" width="440px">
            <el-form label-width="70px">
              <el-form-item label="搜索">
                <el-select
                  v-model="selectedUser"
                  filterable
                  remote
                  :remote-method="searchCandidates"
                  :loading="searchLoading"
                  placeholder="输入用户名搜索"
                  class="w-full"
                >
                  <el-option v-for="c in candidates" :key="c.id" :label="`${c.username} (${c.email ?? ''})`" :value="c" />
                </el-select>
              </el-form-item>
              <el-form-item label="角色">
                <el-select v-model="newRole">
                  <el-option label="访客" value="viewer" />
                  <el-option label="成员" value="member" />
                  <el-option label="管理员" value="admin" />
                </el-select>
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="addOpen = false">取消</el-button>
              <el-button type="primary" :disabled="!selectedUser" @click="submitAdd">添加</el-button>
            </template>
          </el-dialog>
        </div>

        <!-- 危险操作 -->
        <div v-if="activeTab === 'danger'">
          <div class="danger-box">
            <div class="v2-card-title">删除项目</div>
            <p class="v2-aux">删除后不可恢复，将永久移除该项目下的用例、套件、元素与执行记录。</p>
            <el-button v-if="canDeleteProject" type="danger" @click="removeProject">删除项目</el-button>
            <span v-else class="v2-aux">仅 owner 可删除项目</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-layout {
  display: flex;
  gap: 16px;
}
.settings-menu {
  width: 180px;
  border-right: none;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 8px 0;
  flex-shrink: 0;
}
.settings-body {
  flex: 1;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 20px 24px;
}
.form-wrap {
  max-width: var(--form-max-width);
}
.member-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.w-full {
  width: 100%;
}
.danger-box {
  border: 1px solid var(--danger);
  border-radius: var(--radius-card);
  padding: 16px;
  max-width: var(--form-max-width);
}
</style>