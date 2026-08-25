<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppProfileTree from '@/components/AppProfileTree.vue'
import ProfileStatusTag from '@/components/ProfileStatusTag.vue'
import { skipRulesBatch, getAppProfile, type SkipTarget } from '@/api/appProfiles'
import { useAppProfileStore } from '@/stores/appProfile'
import { ElMessage } from 'element-plus'

const route = useRoute()
const projectId = Number(route.params.projectId)
const store = useAppProfileStore()

const skipDialog = {
  visible: false,
  reasonCode: 'unsupported',
  note: '',
  targets: [] as SkipTarget[],
}

async function load() {
  store.projectId = projectId
  await store.loadProfiles()
}

async function expandSuite(row: any) {
  const key = `suite:${row.id}`
  if (!store.expandedKeys.has(key)) {
    await store.loadChildren('suite', row.id)
  }
  store.toggleExpand(key)
}

function openSkip(node: any) {
  skipDialog.targets = targetFor(node)
  skipDialog.visible = true
}

function targetFor(node: any): SkipTarget[] {
  if (node.node_type === 'suite') return [{ type: 'suite', suite_id: node.id! }]
  if (node.node_type === 'case') return [{ type: 'case', case_id: node.id! }]
  return []
}

async function confirmSkip() {
  if (!store.selectedProfileId) return
  if (skipDialog.reasonCode === 'other' && !skipDialog.note.trim()) {
    ElMessage.warning('reason_code=other 时必须填写备注')
    return
  }
  const res = await skipRulesBatch(store.selectedProfileId, {
    expected_revision: store.profileRevision ?? 1,
    operation: 'skip',
    reason: { code: skipDialog.reasonCode, note: skipDialog.note || undefined },
    targets: skipDialog.targets,
  })
  store.markRevision(res.revision_after, store.testAssetRevision ?? 1)
  ElMessage.success(`已跳过 ${res.changed} 项`)
  skipDialog.visible = false
  skipDialog.note = ''
  await store.loadWorkspace()
  store.setStale()
}

async function refreshProfile() {
  if (!store.selectedProfileId) return
  const p = await getAppProfile(store.selectedProfileId)
  store.markRevision(p.revision, store.testAssetRevision ?? 1)
}

function nodeEffective(node: any): string {
  return node.effective_status
}

watch(() => route.params.projectId, load)
onMounted(load)
</script>

<template>
  <div class="profile-workspace">
    <aside class="tree-panel">
      <AppProfileTree :project-id="projectId" />
    </aside>
    <section class="workspace-panel">
      <div class="workspace-head">
        <div class="head-left">
          <span class="profile-name">{{ store.currentProfile?.name ?? '未选档案' }}</span>
          <span class="rev">revision {{ store.profileRevision ?? '-' }}</span>
          <el-button size="small" text @click="refreshProfile">刷新</el-button>
        </div>
        <div class="head-right">
          <el-input
            v-model="store.filters.keyword"
            size="small"
            placeholder="搜索套件"
            clearable
            style="width: 180px"
            @change="store.loadWorkspace"
          />
          <el-select v-model="store.filters.effective_status" size="small" style="width: 130px" @change="store.loadWorkspace">
            <el-option label="全部状态" value="all" />
            <el-option label="正常" value="enabled" />
            <el-option label="已跳过" value="skipped" />
            <el-option label="已覆盖" value="overridden" />
          </el-select>
        </div>
      </div>

      <el-table
        v-loading="store.loading"
        :data="store.suitePage?.items ?? []"
        row-key="id"
        size="small"
        class="suite-table"
        :tree-props="{ children: 'children' }"
      >
        <el-table-column label="名称" min-width="220">
          <template #default="{ row }">
            <span
              class="expandable"
              @click="row.node_type === 'suite' && row.has_children ? expandSuite(row) : null"
            >
              {{ row.node_type === 'suite' ? (store.expandedKeys.has(`suite:${row.id}`) ? '▾' : '▸') : '·' }}
              {{ row.name }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="80">
          <template #default="{ row }">{{ row.node_type }}</template>
        </el-table-column>
        <el-table-column label="生效状态" width="110">
          <template #default="{ row }">
            <ProfileStatusTag :effective-status="nodeEffective(row)" :status-source="row.status_source" />
          </template>
        </el-table-column>
        <el-table-column label="原因" min-width="140">
          <template #default="{ row }">{{ row.reason?.note ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="right">
          <template #default="{ row }">
            <el-button size="small" text type="danger" @click="openSkip(row)">跳过</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!store.loading && (store.suitePage?.items.length ?? 0) === 0" description="暂无套件" />

      <el-dialog v-model="skipDialog.visible" title="批量跳过" width="420px">
        <el-form label-width="80px">
          <el-form-item label="原因">
            <el-select v-model="skipDialog.reasonCode" style="width: 100%">
              <el-option label="不支持" value="unsupported" />
              <el-option label="未适配" value="not_adapted" />
              <el-option label="已废弃" value="deprecated" />
              <el-option label="环境限制" value="environment_limit" />
              <el-option label="其他" value="other" />
            </el-select>
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="skipDialog.note" type="textarea" :rows="2" placeholder="可选，'其他'必填" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="skipDialog.visible = false">取消</el-button>
          <el-button type="primary" @click="confirmSkip">确认跳过</el-button>
        </template>
      </el-dialog>
    </section>
  </div>
</template>

<style scoped>
.profile-workspace {
  display: flex;
  gap: 16px;
  height: calc(100vh - 120px);
}
.tree-panel {
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid var(--el-border-color-light);
  overflow: auto;
}
.workspace-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.workspace-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.profile-name {
  font-weight: 600;
  font-size: 16px;
}
.rev {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.head-right {
  display: flex;
  gap: 8px;
}
.expandable {
  cursor: pointer;
}
</style>
