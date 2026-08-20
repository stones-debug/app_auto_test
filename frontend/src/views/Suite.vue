<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'
import Draggable from 'vuedraggable'

import {
  addSuiteCase,
  createSuite,
  deleteSuite,
  listSuiteCases,
  listSuites,
  removeSuiteCase,
  reorderSuiteCases,
  updateSuite,
  type Suite,
  type SuiteCase,
} from '@/api/suites'
import { listCases } from '@/api/cases'
import RunDialog from '@/components/RunDialog.vue'

const route = useRoute()
const projectId = Number(route.params.projectId)

const suites = ref<Suite[]>([])
const activeSuite = ref<number | null>(null)
const suiteCases = ref<SuiteCase[]>([])
const runDialog = ref<{ open: () => void } | null>(null)
const runningSuite = ref<Suite | null>(null)

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ name: '', description: '' })

const addDialogVisible = ref(false)
const allCases = ref<{ id: number; name: string }[]>([])
const selectedCaseId = ref<number | null>(null)
const caseKeyword = ref('')

async function loadSuites() {
  suites.value = await listSuites(projectId)
  if (!activeSuite.value && suites.value.length > 0) {
    await selectSuite(suites.value[0].id)
  }
}

async function selectSuite(id: number) {
  activeSuite.value = id
  suiteCases.value = await listSuiteCases(id)
}

function openCreate() {
  editingId.value = null
  form.value = { name: '', description: '' }
  dialogVisible.value = true
}

function openEdit(suite: Suite) {
  editingId.value = suite.id
  form.value = { name: suite.name, description: suite.description ?? '' }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.name) {
    ElMessage.warning('请输入套件名称')
    return
  }
  if (editingId.value) {
    await updateSuite(editingId.value, form.value)
  } else {
    const suite = await createSuite(projectId, form.value)
    activeSuite.value = suite.id
  }
  dialogVisible.value = false
  await loadSuites()
}

async function remove(suite: Suite) {
  await ElMessageBox.confirm(`确认删除套件「${suite.name}」？`, '提示', { type: 'warning' })
  await deleteSuite(suite.id)
  if (activeSuite.value === suite.id) activeSuite.value = null
  await loadSuites()
}

async function openAddCase() {
  caseKeyword.value = ''
  selectedCaseId.value = null
  allCases.value = []
  const data = await listCases(projectId, { page: 1, page_size: 200 })
  const inSuite = new Set(suiteCases.value.map((c) => c.case_id))
  allCases.value = data.items.filter((c) => !inSuite.has(c.id))
  addDialogVisible.value = true
}

async function addCase() {
  if (!selectedCaseId.value) {
    ElMessage.warning('请选择用例')
    return
  }
  await addSuiteCase(activeSuite.value!, selectedCaseId.value)
  addDialogVisible.value = false
  await selectSuite(activeSuite.value!)
}

async function removeCase(suiteCase: SuiteCase) {
  await removeSuiteCase(activeSuite.value!, suiteCase.case_id)
  await selectSuite(activeSuite.value!)
}

async function onReorder() {
  if (!activeSuite.value) return
  await reorderSuiteCases(activeSuite.value, suiteCases.value.map((c) => c.case_id))
  await selectSuite(activeSuite.value)
}

function openRun(suite: Suite) {
  runningSuite.value = suite
  runDialog.value?.open()
}

onMounted(loadSuites)
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="9">
      <div class="suite-list">
        <div class="toolbar">
          <el-button type="primary" plain @click="openCreate">新建套件</el-button>
        </div>
        <div
          v-for="s in suites"
          :key="s.id"
          class="suite-item"
          :class="{ active: s.id === activeSuite }"
          @click="selectSuite(s.id)"
        >
          <div class="suite-name">{{ s.name }}</div>
          <div class="suite-meta">
            <el-tag size="small" type="info">{{ s.case_count }} 个用例</el-tag>
            <span class="suite-actions">
              <el-button size="small" type="success" text @click.stop="openRun(s)">运行</el-button>
              <el-button size="small" text @click.stop="openEdit(s)">编辑</el-button>
              <el-button size="small" type="danger" text @click.stop="remove(s)">删除</el-button>
            </span>
          </div>
        </div>
        <el-empty v-if="suites.length === 0" description="暂无套件" />
      </div>
    </el-col>

    <el-col :span="15">
      <template v-if="activeSuite">
        <div class="toolbar">
          <el-button type="primary" @click="openAddCase">添加用例</el-button>
        </div>
        <Draggable v-model="suiteCases" item-key="id" handle=".drag-handle" @end="onReorder">
          <template #item="{ element }">
            <el-card class="case-item" shadow="never">
              <div class="case-row">
                <span class="drag-handle">⠿</span>
                <span class="case-name">{{ element.case_name }}</span>
                <el-tag v-if="element.module_name" size="small">{{ element.module_name }}</el-tag>
                <el-button size="small" type="danger" text @click="removeCase(element)">移除</el-button>
              </div>
            </el-card>
          </template>
        </Draggable>
        <el-empty v-if="suiteCases.length === 0" description="该套件暂无用例，点击「添加用例」" />
      </template>
      <el-empty v-else description="请选择左侧套件" />
    </el-col>
  </el-row>

  <el-dialog v-model="dialogVisible" :title="editingId ? '编辑套件' : '新建套件'" width="480px">
    <el-form label-width="80px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="form.description" type="textarea" :rows="3" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="addDialogVisible" title="添加用例" width="520px">
    <el-input v-model="caseKeyword" placeholder="搜索用例名" clearable />
    <el-select v-model="selectedCaseId" filterable placeholder="选择用例" class="full case-select">
      <el-option
        v-for="c in allCases.filter((c) => !caseKeyword || c.name.includes(caseKeyword))"
        :key="c.id"
        :label="c.name"
        :value="c.id"
      />
    </el-select>
    <template #footer>
      <el-button @click="addDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="addCase">添加</el-button>
    </template>
  </el-dialog>

  <RunDialog
    v-if="runningSuite"
    ref="runDialog"
    :type="'suite'"
    :id="runningSuite.id"
    :name="runningSuite.name"
  />
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
.suite-list {
  border-right: 1px solid #eee;
  padding-right: 12px;
}
.suite-item {
  padding: 10px 12px;
  border: 1px solid #eee;
  border-radius: 6px;
  margin-bottom: 8px;
  cursor: pointer;
}
.suite-item.active {
  border-color: #409eff;
  background: #ecf5ff;
}
.suite-name {
  font-weight: 600;
  margin-bottom: 6px;
}
.suite-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.case-item {
  margin-bottom: 8px;
}
.case-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.drag-handle {
  cursor: move;
  color: #999;
}
.case-name {
  flex: 1;
}
.full {
  width: 100%;
}
.case-select {
  margin-top: 12px;
}
</style>