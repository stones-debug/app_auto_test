<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'

import {
  LOCATOR_TYPES,
  createElement,
  deleteElement,
  elementUsage,
  listElements,
  updateElement,
  type TestElement,
} from '@/api/elements'

const route = useRoute()
const projectId = Number(route.params.projectId)

const loading = ref(false)
const items = ref<TestElement[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const platform = ref('')

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = ref<Partial<TestElement>>({
  name: '',
  page_name: '',
  platform: 'both',
  locator_type: 'id',
  locator_value: '',
  description: '',
})

const usageDialogVisible = ref(false)
const usageCases = ref<{ case_id: number; case_name: string }[]>([])

async function load() {
  loading.value = true
  try {
    const data = await listElements(projectId, {
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value,
      platform: platform.value,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.value = {
    name: '',
    page_name: '',
    platform: 'both',
    locator_type: 'id',
    locator_value: '',
    description: '',
  }
  dialogVisible.value = true
}

function openEdit(row: TestElement) {
  editingId.value = row.id
  form.value = {
    name: row.name,
    page_name: row.page_name ?? '',
    platform: row.platform ?? 'both',
    locator_type: row.locator_type,
    locator_value: row.locator_value,
    description: row.description ?? '',
  }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.name || !form.value.locator_value) {
    ElMessage.warning('名称和定位值必填')
    return
  }
  if (editingId.value) {
    await updateElement(editingId.value, form.value)
    ElMessage.success('已更新')
  } else {
    await createElement(projectId, form.value)
    ElMessage.success('已创建')
  }
  dialogVisible.value = false
  await load()
}

async function remove(row: TestElement) {
  await ElMessageBox.confirm(`确认删除元素「${row.name}」？`, '提示', { type: 'warning' })
  await deleteElement(row.id)
  ElMessage.success('已删除')
  await load()
}

async function showUsage(row: TestElement) {
  usageCases.value = await elementUsage(row.id)
  usageDialogVisible.value = true
}

function locatorLabel(type: string) {
  return LOCATOR_TYPES.find((t) => t.value === type)?.label ?? type
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="按名称搜索" clearable class="search" @keyup.enter="page = 1; load()" />
      <el-select v-model="platform" placeholder="平台" clearable class="platform" @change="page = 1; load()">
        <el-option label="Android" value="android" />
        <el-option label="iOS" value="ios" />
        <el-option label="通用" value="both" />
      </el-select>
      <el-button type="primary" @click="page = 1; load()">搜索</el-button>
      <el-button type="primary" plain @click="openCreate">新建元素</el-button>
    </div>

    <el-table v-loading="loading" :data="items">
      <el-table-column prop="name" label="名称" min-width="150" />
      <el-table-column prop="page_name" label="页面" min-width="120" />
      <el-table-column label="平台" width="90">
        <template #default="{ row }">
          <el-tag size="small">{{ row.platform }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="定位方式" width="160">
        <template #default="{ row }">{{ locatorLabel(row.locator_type) }}</template>
      </el-table-column>
      <el-table-column prop="locator_value" label="定位值" min-width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text @click="showUsage(row)">引用</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑元素' : '新建元素'" width="560px">
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="页面">
          <el-input v-model="form.page_name" placeholder="所属页面名" />
        </el-form-item>
        <el-form-item label="平台">
          <el-radio-group v-model="form.platform">
            <el-radio value="android">Android</el-radio>
            <el-radio value="ios">iOS</el-radio>
            <el-radio value="both">通用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="定位方式">
          <el-select v-model="form.locator_type" class="full">
            <el-option v-for="t in LOCATOR_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="定位值" required>
          <el-input v-model="form.locator_value" placeholder="如 com.demo:id/btn_login / //*[@text='登录']" />
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

    <el-dialog v-model="usageDialogVisible" title="被以下用例引用" width="480px">
      <el-empty v-if="usageCases.length === 0" description="暂无用例引用" />
      <el-table v-else :data="usageCases">
        <el-table-column prop="case_id" label="用例ID" width="90" />
        <el-table-column prop="case_name" label="用例名" />
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.search {
  width: 220px;
}
.platform {
  width: 130px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.full {
  width: 100%;
}
</style>