<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Delete, Edit, Folder, Plus, Rank } from '@element-plus/icons-vue'

import { type ModuleScope } from '@/api/modules'
import { useModuleTree } from '@/composables/useModuleTree'
import type { ModuleKey } from '@/utils/moduleFilter'
import { collectSubtreeIds, type ModuleNode } from '@/utils/moduleTree'
import { resolveDropPosition, type DropPosition } from '@/utils/moduleTreeDrag'

const props = withDefaults(
  defineProps<{
    projectId: number
    scope: ModuleScope
    title: string
    selectedKey: ModuleKey
    writable?: boolean
  }>(),
  { writable: false },
)

const emit = defineEmits<{
  select: [key: ModuleKey]
  /** 模块树发生任何变更（新建/改名/删除/移动），调用方据此刷新列表 */
  mutated: []
}>()

const tree = useModuleTree(props.projectId, props.scope)
const { modules, rows, loading, collapsed, load, toggleCollapse } = tree

const scopeLabel = computed(() => (props.scope === 'case' ? '用例' : '套件'))
const allLabel = computed(() => `全部${scopeLabel.value}`)

onMounted(() => {
  void load()
})

function isCollapsed(moduleId: number) {
  return collapsed.value.has(moduleId)
}

function select(key: ModuleKey) {
  emit('select', key)
}

// ---------- 新建 / 编辑 / 删除 ----------

const dialogVisible = ref(false)
const dialogSaving = ref(false)
const editingModuleId = ref<number | null>(null)
const creatingParentId = ref<number | null>(null)
const formName = ref('')
const creatingParentName = computed(
  () => modules.value.find((module) => module.id === creatingParentId.value)?.name ?? '',
)
const dialogTitle = computed(() =>
  editingModuleId.value != null ? '编辑模块' : creatingParentId.value != null ? '新建子模块' : '新增模块',
)

const contextMenu = ref<{
  visible: boolean
  left: number
  top: number
  module: ModuleNode | null
}>({ visible: false, left: 0, top: 0, module: null })

function closeContextMenu() {
  contextMenu.value.visible = false
}

function openContextMenu(event: MouseEvent, node: ModuleNode) {
  if (!props.writable) return
  event.preventDefault()
  event.stopPropagation()
  contextMenu.value = {
    visible: true,
    left: Math.min(event.clientX, Math.max(8, window.innerWidth - 190)),
    top: Math.min(event.clientY, Math.max(8, window.innerHeight - 150)),
    module: node,
  }
}

function openCreate(parentId: number | null = null) {
  closeContextMenu()
  editingModuleId.value = null
  creatingParentId.value = parentId
  formName.value = ''
  dialogVisible.value = true
}

function openEdit(node: ModuleNode) {
  closeContextMenu()
  editingModuleId.value = node.id
  creatingParentId.value = null
  formName.value = node.name
  dialogVisible.value = true
}

async function submitModule() {
  const name = formName.value.trim()
  if (!name) {
    ElMessage.warning('请输入模块名称')
    return
  }
  dialogSaving.value = true
  try {
    const editingId = editingModuleId.value
    if (editingId != null) {
      await tree.rename(editingId, name)
      ElMessage.success('模块已更新')
    } else {
      const created = await tree.create(name, creatingParentId.value)
      emit('select', String(created.id) as ModuleKey)
      ElMessage.success('模块已创建')
    }
    dialogVisible.value = false
    editingModuleId.value = null
    creatingParentId.value = null
    emit('mutated')
  } finally {
    dialogSaving.value = false
  }
}

async function removeModule(node: ModuleNode) {
  closeContextMenu()
  await ElMessageBox.confirm(
    `确认删除模块「${node.name}」？其子模块会提升到当前层级，模块内${scopeLabel.value}会变为未分组。`,
    '提示',
    { type: 'warning' },
  )
  await tree.remove(node.id)
  if (props.selectedKey === String(node.id)) emit('select', 'all')
  emit('mutated')
  ElMessage.success('模块已删除')
}

// ---------- 拖拽 ----------

const dragSourceId = ref<number | null>(null)
const dropHint = ref<{ moduleId: number; position: DropPosition } | null>(null)
const rootDropActive = ref(false)

function onDragStart(event: DragEvent, node: ModuleNode) {
  if (!props.writable) return
  dragSourceId.value = node.id
  dropHint.value = null
  if (event.dataTransfer) {
    // Firefox 必须写入数据才会启动拖拽
    event.dataTransfer.setData('text/plain', String(node.id))
    event.dataTransfer.effectAllowed = 'move'
  }
}

function onDragOver(event: DragEvent, node: ModuleNode) {
  const draggedId = dragSourceId.value
  if (draggedId == null || !props.writable) return
  const element = event.currentTarget as HTMLElement
  const rect = element.getBoundingClientRect()
  const position = resolveDropPosition(event.clientY - rect.top, rect.height)
  const target = { moduleId: node.id, position }
  if (!tree.canDrop(draggedId, target)) {
    // 非法落点：不 preventDefault，浏览器显示禁止光标
    dropHint.value = null
    return
  }
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  dropHint.value = target
}

async function onDrop(event: DragEvent, node: ModuleNode) {
  event.preventDefault()
  const draggedId = dragSourceId.value
  const hint = dropHint.value
  if (draggedId == null || !hint || hint.moduleId !== node.id) return
  await commitDrop(() => tree.drop(draggedId, hint))
}

function onDragLeave(_event: DragEvent, node: ModuleNode) {
  if (dropHint.value?.moduleId === node.id) dropHint.value = null
}

function onDragEnd() {
  dragSourceId.value = null
  dropHint.value = null
  rootDropActive.value = false
}

function onRootDragOver(event: DragEvent) {
  const draggedId = dragSourceId.value
  if (draggedId == null || !props.writable) return
  if (!tree.canDropToRoot(draggedId)) {
    rootDropActive.value = false
    return
  }
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  rootDropActive.value = true
}

async function onRootDrop(event: DragEvent) {
  event.preventDefault()
  const draggedId = dragSourceId.value
  rootDropActive.value = false
  if (draggedId == null) return
  await commitDrop(() => tree.dropToRoot(draggedId))
}

async function commitDrop(action: () => Promise<boolean>) {
  try {
    const moved = await action()
    if (moved) {
      emit('mutated')
      ElMessage.success('模块已移动')
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模块移动失败')
  } finally {
    onDragEnd()
  }
}

function dropClass(node: ModuleNode) {
  const hint = dropHint.value
  if (!hint || hint.moduleId !== node.id) return ''
  return `drop-${hint.position}`
}

// ---------- 移动到…（触屏 / 键盘兜底） ----------

const moveDialogVisible = ref(false)
const moveSaving = ref(false)
const moveSource = ref<ModuleNode | null>(null)
const moveTargetValue = ref(0)

interface MoveOption {
  value: number
  label: string
  disabled: boolean
}

const moveOptions = computed<MoveOption[]>(() => {
  const sourceId = moveSource.value?.id
  const blocked = sourceId != null ? collectSubtreeIds(modules.value, sourceId) : new Set<number>()
  const result: MoveOption[] = [{ value: 0, label: '根层级（顶级模块）', disabled: false }]
  const walk = (nodes: ModuleNode[], prefix: string) => {
    for (const node of nodes) {
      const label = prefix ? `${prefix} / ${node.name}` : node.name
      result.push({ value: node.id, label, disabled: blocked.has(node.id) })
      walk(node.children, label)
    }
  }
  walk(tree.tree.value, '')
  return result
})

function openMove(node: ModuleNode) {
  closeContextMenu()
  moveSource.value = node
  moveTargetValue.value = 0
  moveDialogVisible.value = true
}

async function submitMove() {
  const source = moveSource.value
  if (!source) return
  moveSaving.value = true
  try {
    const moved = await tree.moveTo(source.id, moveTargetValue.value === 0 ? null : moveTargetValue.value)
    moveDialogVisible.value = false
    if (moved) {
      emit('mutated')
      ElMessage.success('模块已移动')
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模块移动失败')
  } finally {
    moveSaving.value = false
  }
}

onMounted(() => {
  window.addEventListener('click', closeContextMenu)
})

onUnmounted(() => {
  window.removeEventListener('click', closeContextMenu)
})
</script>

<template>
  <div class="module-tree">
    <div class="tree-head v2-card-title">{{ title }}</div>
    <div
      class="tree-item"
      :class="{ active: selectedKey === 'all' }"
      @click="select('all')"
    >
      {{ allLabel }}
    </div>
    <div
      class="tree-item"
      :class="{ active: selectedKey === 'none' }"
      @click="select('none')"
    >
      未分组
    </div>

    <div v-loading="loading" class="tree-body">
      <div
        v-for="row in rows"
        :key="row.node.id"
        class="tree-item tree-module"
        :class="[
          { active: selectedKey === String(row.node.id) },
          dropClass(row.node),
        ]"
        :style="{ paddingLeft: `${12 + row.level * 18}px` }"
        :draggable="writable"
        @click="select(String(row.node.id) as ModuleKey)"
        @contextmenu="openContextMenu($event, row.node)"
        @dragstart="onDragStart($event, row.node)"
        @dragover="onDragOver($event, row.node)"
        @dragleave="onDragLeave($event, row.node)"
        @drop="onDrop($event, row.node)"
        @dragend="onDragEnd"
      >
        <span class="tree-label">
          <span
            class="tree-chevron"
            :class="{ expanded: !isCollapsed(row.node.id) }"
            :style="{ visibility: row.node.children.length ? 'visible' : 'hidden' }"
            title="展开/折叠"
            @click.stop="toggleCollapse(row.node.id)"
          >›</span>
          <el-icon class="module-folder-icon"><Folder /></el-icon>
          <span class="module-name">{{ row.node.name }}</span>
        </span>
        <span v-if="writable" class="module-actions">
          <el-icon title="编辑模块" @click.stop="openEdit(row.node)"><Edit /></el-icon>
          <el-icon class="module-delete" title="删除模块" @click.stop="removeModule(row.node)"><Delete /></el-icon>
        </span>
      </div>

      <div
        v-if="writable && dragSourceId != null"
        class="tree-root-drop"
        :class="{ active: rootDropActive }"
        @dragover="onRootDragOver"
        @dragleave="rootDropActive = false"
        @drop="onRootDrop"
      >
        拖到此处成为顶级模块
      </div>
    </div>

    <el-button v-if="writable" class="add-module" text type="primary" @click="openCreate()">
      <el-icon><Plus /></el-icon>
      <span>新增模块</span>
    </el-button>

    <div
      v-if="contextMenu.visible && contextMenu.module"
      class="module-context-menu"
      :style="{ left: `${contextMenu.left}px`, top: `${contextMenu.top}px` }"
      @click.stop
    >
      <button type="button" @click="openCreate(contextMenu.module!.id)">新建子模块</button>
      <button type="button" @click="openEdit(contextMenu.module!)">编辑模块</button>
      <button type="button" @click="openMove(contextMenu.module!)">
        <el-icon><Rank /></el-icon>
        移动到…
      </button>
      <button type="button" @click="removeModule(contextMenu.module!)">删除模块</button>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="420px" append-to-body>
      <el-form label-width="80px" @submit.prevent="submitModule">
        <div v-if="creatingParentName" class="module-parent-hint">父模块：{{ creatingParentName }}</div>
        <el-form-item label="模块名称" required>
          <el-input
            v-model="formName"
            placeholder="请输入模块名称"
            maxlength="255"
            @keyup.enter="submitModule"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="dialogSaving" :disabled="!formName.trim()" @click="submitModule">
          {{ editingModuleId != null ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="moveDialogVisible" title="移动到…" width="420px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="目标模块">
          <el-select v-model="moveTargetValue" placeholder="请选择目标模块" class="move-select">
            <el-option
              v-for="option in moveOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
              :disabled="option.disabled"
            />
          </el-select>
        </el-form-item>
        <div class="move-hint v2-aux">模块会成为目标模块的子模块；选择「根层级」则提升为顶级模块。</div>
      </el-form>
      <template #footer>
        <el-button @click="moveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="moveSaving" @click="submitMove">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.module-tree {
  width: 260px;
  flex-shrink: 0;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px;
  align-self: flex-start;
  max-height: calc(100vh - 150px);
  overflow-y: auto;
  box-sizing: border-box;
}
.tree-head {
  padding: 8px 12px;
}
.tree-body {
  min-height: 4px;
}
.tree-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--text-2);
  font-size: 13px;
}
.tree-item:hover {
  background: var(--primary-light);
}
.tree-item.active {
  background: var(--primary-light);
  color: var(--primary);
  font-weight: 600;
}
.tree-module {
  position: relative;
}
/* 拖拽落点提示：上下为插入线，中间为“成为子模块” */
.tree-module.drop-before::before,
.tree-module.drop-after::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  height: 2px;
  background: var(--primary);
  border-radius: 1px;
}
.tree-module.drop-before::before {
  top: 0;
}
.tree-module.drop-after::after {
  bottom: 0;
}
.tree-module.drop-inside {
  outline: 1px dashed var(--primary);
  outline-offset: -1px;
  background: var(--primary-light);
}
.tree-module[draggable='true'] {
  cursor: grab;
}
.tree-module.tree-module:active {
  cursor: grabbing;
}
.tree-root-drop {
  margin: 6px 8px 0;
  padding: 6px 8px;
  border: 1px dashed var(--border);
  border-radius: 6px;
  color: var(--text-2);
  font-size: 12px;
  text-align: center;
}
.tree-root-drop.active {
  border-color: var(--primary);
  background: var(--primary-light);
  color: var(--primary);
}
.tree-label {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 4px;
}
.tree-chevron {
  width: 14px;
  flex: 0 0 14px;
  color: var(--text-2);
  font-size: 20px;
  line-height: 12px;
  text-align: center;
  cursor: pointer;
  transform: rotate(0deg);
  transition: transform 0.15s ease;
}
.tree-chevron.expanded {
  transform: rotate(90deg);
}
.module-folder-icon {
  color: #e6b800;
  flex: 0 0 auto;
}
.module-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.module-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-2);
  font-size: 13px;
  opacity: 0;
}
.tree-module:hover .module-actions,
.tree-module.active .module-actions {
  opacity: 1;
}
.module-actions .el-icon {
  cursor: pointer;
}
.module-actions .el-icon:hover {
  color: var(--primary);
}
.module-actions .module-delete:hover {
  color: var(--el-color-danger);
}
.module-context-menu {
  position: fixed;
  z-index: 3000;
  min-width: 150px;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card-bg);
  box-shadow: var(--shadow-md);
}
.module-context-menu button {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
  font-size: 13px;
}
.module-context-menu button:hover {
  background: var(--primary-light);
  color: var(--primary);
}
.module-parent-hint {
  margin: 0 0 10px 80px;
  color: var(--text-2);
  font-size: 12px;
}
.move-select {
  width: 100%;
}
.move-hint {
  margin: 0 0 10px 80px;
  font-size: 12px;
}
.add-module {
  width: 100%;
  margin-top: 8px;
  justify-content: center;
  border: 1px dashed var(--border);
  border-radius: 6px;
}
</style>
