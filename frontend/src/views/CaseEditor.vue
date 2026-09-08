<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  CASE_STATUS,
  actionMeta,
  createCase,
  getCase,
  normalizeFlowNode,
  updateCase,
  validateStep,
  validateAssertion,
  type FlowNode,
  type StepPhase,
  type TestCase,
} from '@/api/cases'
import { getElement, listElements, listModules } from '@/api/elements'
import CaseFlowEditor from '@/components/CaseFlowEditor.vue'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'
import { usePermission } from '@/composables/usePermission'
import { useProjectContextStore } from '@/stores/projectContext'
import { buildCaseEditorSummary, caseEditorSummaryText } from '@/utils/caseEditorSummary'
import { caseListQuery, moduleKeyFromId, parseCaseListPage, parseCaseListPageSize, parseModuleKey, type CaseModuleKey } from '@/utils/caseModuleNavigation'
import { mergeFlowNodes, normalizeFlowNodeOrders } from '@/utils/flowNodeOrder'
import { parseSuiteReturnId, suiteLocation } from '@/utils/suiteNavigation'

const route = useRoute()
const router = useRouter()
const projectContext = useProjectContextStore()
const { canWriteAssets } = usePermission()
const projectId = Number(route.params.projectId)
const caseId = computed<number | null>(() => {
  if (route.name === 'CaseNew') return null
  const p = route.params.caseId
  if (p == null || p === 'new') return null
  const n = Number(p)
  return Number.isFinite(n) ? n : null
})

const loading = ref(false)
const modules = ref<{ id: number; name: string }[]>([])

const form = reactive<Partial<TestCase>>({
  name: '',
  module_id: null,
  description: '',
  status: 'draft',
  flow_nodes: [] as FlowNode[],
  variables: {} as Record<string, unknown>,
})

const variableEntries = ref<{ key: string; value: string }[]>([])
const isEdit = computed(() => caseId.value !== null)
// 返回列表时保留进入编辑页前的筛选与页码；新建成功后模块筛选按已保存模块返回。
const returnModuleKey = ref<CaseModuleKey>(parseModuleKey(route.query.module))
const returnPage = parseCaseListPage(route.query.page)
const returnPageSize = parseCaseListPageSize(route.query.page_size)
const returnSuiteId = parseSuiteReturnId(route.query)

function casesLocation(moduleKey: CaseModuleKey) {
  return { path: `/projects/${projectId}/cases`, query: caseListQuery(moduleKey, returnPage, returnPageSize) }
}

function returnLocation(moduleKey: CaseModuleKey) {
  return returnSuiteId === null ? casesLocation(moduleKey) : suiteLocation(projectId, returnSuiteId)
}

function goBack() {
  void router.push(returnLocation(returnModuleKey.value))
}

// V2 §4.2：编辑页 dirty 离开确认
const { markDirty, markSaved } = useUnsavedChanges()
watch(
  () => JSON.stringify({ ...form, variables: collectVariables() }),
  () => {
    if (loadedOnce.value) markDirty()
  },
  { deep: true },
)
const loadedOnce = ref(false)

function phaseNodes(phase: StepPhase) {
  return computed<FlowNode[]>({
    get: () => (form.flow_nodes as FlowNode[]).filter((node) => (node.phase ?? 'main') === phase),
    set: (nodes) => {
      form.flow_nodes = mergeFlowNodes((form.flow_nodes as FlowNode[]) ?? [], phase, nodes)
    },
  })
}

const setupNodes = phaseNodes('setup')
const mainNodes = phaseNodes('main')
const teardownNodes = phaseNodes('teardown')

// 收起/展开：编辑完成后可折叠为一行简略信息，点击展开
const collapsed = ref(false)

const moduleName = computed(() => {
  if (form.module_id == null) return '未分组'
  return modules.value.find((m) => m.id === form.module_id)?.name ?? '未分组'
})

const summaryMeta = computed(() => {
  return buildCaseEditorSummary(
    (form.flow_nodes as FlowNode[]) ?? [],
    variableEntries.value,
  )
})

const summaryText = computed(() => caseEditorSummaryText(moduleName.value, summaryMeta.value))

function statusLabel(s: string | undefined) {
  return CASE_STATUS.find((x) => x.value === s)?.label ?? s
}

function statusType(s: string | undefined): 'info' | 'success' | 'danger' {
  if (s === 'active') return 'success'
  if (s === 'disabled') return 'danger'
  return 'info'
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

function addVariable() {
  variableEntries.value.push({ key: '', value: '' })
}

function removeVariable(index: number) {
  variableEntries.value.splice(index, 1)
}

function collectVariables(): Record<string, unknown> {
  const vars: Record<string, unknown> = {}
  for (const entry of variableEntries.value) {
    if (entry.key) vars[entry.key] = entry.value
  }
  return vars
}

async function save() {
  if (!form.name) {
    ElMessage.warning('请输入用例名称')
    return
  }
  const invalidNode = (form.flow_nodes as FlowNode[])
    .map((node, index) => ({ index, message: node.kind === 'action' ? validateStep(node) : validateAssertion(node) }))
    .find((item) => item.message)
  if (invalidNode?.message) {
    ElMessage.warning(`第 ${invalidNode.index + 1} 个节点：${invalidNode.message}`)
    return
  }
  loading.value = true
  try {
    const flowNodes = normalizeFlowNodeOrders(
      ((form.flow_nodes as FlowNode[]) ?? []).map(normalizeFlowNode),
    )
    const payload: Partial<TestCase> = {
      name: form.name,
      module_id: form.module_id,
      description: form.description,
      status: form.status,
      flow_nodes: flowNodes,
      variables: collectVariables(),
    }
    if (isEdit.value) {
      await updateCase(caseId.value!, payload)
      ElMessage.success('已保存')
      markSaved()
      void router.push(returnLocation(returnModuleKey.value))
    } else {
      await createCase(projectId, payload)
      ElMessage.success('已创建')
      markSaved()
      void router.push(returnLocation(moduleKeyFromId(form.module_id)))
    }
  } finally {
    loading.value = false
  }
}

// 元素 id → 名称映射：收起摘要显示元素名（而非编号），加载失败时回退为编号展示
const elementNames = ref(new Map<number, string>())

function elementIds(nodes: FlowNode[]): number[] {
  const ids = new Set<number>()
  for (const node of nodes) {
    if (node.element_id != null) ids.add(node.element_id)
    if (node.kind === 'action') {
      for (const field of actionMeta(node.action).fields) {
        if (field.type !== 'element') continue
        const value = node.params?.[field.key]
        if (value != null && value !== '') ids.add(Number(value))
      }
    }
  }
  return [...ids]
}

async function loadElementNames(nodes: FlowNode[]) {
  const data = await listElements({ page: 1, page_size: 200, project_id: projectId })
  const map = new Map(data.items.map((element) => [element.id, element.name]))
  const missingIds = elementIds(nodes).filter((id) => !map.has(id))
  const missingElements = await Promise.all(
    missingIds.map(async (id) => {
      try {
        const element = await getElement(id)
        return element.project_id === projectId ? element : null
      } catch {
        return null
      }
    }),
  )
  for (const element of missingElements) {
    if (element) map.set(element.id, element.name)
  }
  elementNames.value = map
}

onMounted(async () => {
  await projectContext.load(projectId)
  if (!canWriteAssets.value) {
    ElMessage.warning('当前项目角色无权编辑用例')
    await router.replace(`/projects/${projectId}/cases`)
    return
  }
  modules.value = await listModules(projectId)
  if (isEdit.value) {
    const data = await getCase(caseId.value!)
    form.name = data.name
    form.module_id = data.module_id
    form.description = data.description ?? ''
    form.status = data.status
    // Step 4：加载旧数据时归一化 continue_on_failure，且清理历史留在 params 里的字段
    form.flow_nodes = (data.flow_nodes ?? data.steps ?? []).map((node) => normalizeFlowNode(node as FlowNode))
    form.variables = data.variables
    variableEntries.value = Object.entries(data.variables).map(([key, value]) => ({
      key,
      value: String(value),
    }))
  }
  try {
    await loadElementNames((form.flow_nodes as FlowNode[]) ?? [])
  } catch {
    // 元素列表加载失败不影响用例编辑，摘要回退显示元素编号
  }
  loadedOnce.value = true
})
</script>

<template>
  <div v-loading="loading">
    <!-- 收起/展开摘要条：折叠后仅显示一行用例简略信息 -->
    <div
      class="editor-summary"
      :class="{ collapsed }"
      role="button"
      tabindex="0"
      :aria-expanded="!collapsed"
      aria-controls="case-editor-body"
      @click="toggleCollapsed"
      @keydown.enter.prevent="toggleCollapsed"
      @keydown.space.prevent="toggleCollapsed"
    >
      <span class="sum-icon">{{ collapsed ? '▸' : '▾' }}</span>
      <span class="sum-name">{{ form.name || '未命名用例' }}</span>
      <el-tag :type="statusType(form.status)" size="small">{{ statusLabel(form.status) }}</el-tag>
      <span class="sum-meta" :title="summaryText">{{ summaryText }}</span>
      <span class="sum-hint">{{ collapsed ? '点击展开' : '点击收起' }}</span>
    </div>

    <div id="case-editor-body" v-show="!collapsed" class="editor-body">
      <div class="content-card mb16">
        <div class="section-title">基本信息</div>
        <el-form label-width="80px" class="basic-form">
          <el-form-item label="名称" required>
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="模块">
            <el-select v-model="form.module_id" clearable placeholder="选择模块" class="w-200">
              <el-option v-for="m in modules" :key="m.id" :label="m.name" :value="m.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-radio-group v-model="form.status">
              <el-radio v-for="s in CASE_STATUS" :key="s.value" :value="s.value">{{ s.label }}</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="form.description" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>
      </div>

    <CaseFlowEditor
      v-model="setupNodes"
      :project-id="projectId"
      phase="setup"
      title="前置操作"
      description="运行时勾选后，在每个用例主体步骤之前执行"
      tone="warning"
      :element-names="elementNames"
    />

    <CaseFlowEditor
      v-model="mainNodes"
      :project-id="projectId"
      phase="main"
      title="执行步骤"
      description="用例的主体操作，始终执行"
      tone="primary"
      :element-names="elementNames"
    />

    <CaseFlowEditor
      v-model="teardownNodes"
      :project-id="projectId"
      phase="teardown"
      title="后置操作"
      description="运行时勾选后，在主体步骤之后执行；主体失败时仍会尝试清理"
      tone="success"
      :element-names="elementNames"
    />

    <div class="content-card mb16">
      <div class="section-title-row">
        <span class="section-title">用例变量</span>
        <el-button type="primary" size="small" @click="addVariable">添加变量</el-button>
      </div>
      <div v-for="(entry, idx) in variableEntries" :key="idx" class="variable-row">
        <el-input v-model="entry.key" placeholder="变量名" class="var-name" />
        <el-input v-model="entry.value" placeholder="变量值" class="var-value" />
        <el-button type="danger" text @click="removeVariable(idx)">删除</el-button>
      </div>
      <div class="add-more">
        <el-button type="primary" plain class="w-full" @click="addVariable">+ 添加变量</el-button>
      </div>
    </div>

    <div class="footer">
      <el-button @click="goBack">返回</el-button>
      <el-button type="primary" @click="save">保存</el-button>
    </div>
    </div>
  </div>
</template>

<style scoped>
.mb16 {
  margin-bottom: 16px;
}
.editor-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 16px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.editor-summary:hover {
  border-color: var(--primary);
  box-shadow: 0 2px 8px rgba(79, 70, 229, 0.1);
}
.editor-summary:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.editor-summary.collapsed {
  margin-bottom: 0;
}
.sum-icon {
  color: var(--primary);
  font-size: 14px;
  flex-shrink: 0;
}
.sum-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}
.sum-meta {
  color: var(--text-2);
  font-size: 13px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sum-hint {
  margin-left: auto;
  color: var(--primary);
  font-size: 12px;
  flex-shrink: 0;
}
.editor-body {
  display: flex;
  flex-direction: column;
}
@media (max-width: 768px) {
  .editor-summary {
    gap: 8px;
    padding: 10px 12px;
  }
  .sum-name {
    max-width: 34%;
  }
  .sum-hint {
    display: none;
  }
}
.content-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 14px;
}
.section-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.section-title-row .section-title {
  margin-bottom: 0;
}
.basic-form {
  max-width: 720px;
}
.w-200 {
  width: 200px;
}
.step-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.step-card {
  margin-bottom: 0;
  border-radius: 8px;
}
.step-card.step {
  border-left: 3px solid var(--primary);
}
.step-card.assertion {
  border-left: 3px solid var(--success);
}
.step-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.drag-handle {
  cursor: move;
  color: #999;
}
.drag-handle:hover {
  color: var(--primary);
}
.step-badge {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.step-badge.step {
  background: var(--primary-light);
  color: var(--primary);
}
.step-badge.assertion {
  background: rgba(16, 185, 129, 0.12);
  color: var(--success);
}
.action-select {
  flex: 1;
  max-width: 220px;
}
.continue-label {
  color: #888;
  font-size: 13px;
  flex-shrink: 0;
}
.step-body {
  margin-top: 8px;
}
.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.field-label {
  width: 80px;
  color: #888;
  flex-shrink: 0;
}
.variable-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  max-width: 560px;
}
.var-name {
  width: 180px;
}
.var-value {
  flex: 1;
}
.add-more {
  margin-top: 10px;
}
.add-more .w-full {
  width: 100%;
  border-style: dashed;
}
.footer {
  position: sticky;
  bottom: 0;
  background: #fff;
  border-top: 1px solid var(--border);
  padding: 14px 0;
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  z-index: 10;
}
</style>
