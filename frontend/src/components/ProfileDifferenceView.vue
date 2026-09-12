<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { differences, type DifferenceRow } from '@/api/appProfiles'

const props = defineProps<{ profileId: number }>()
const visible = defineModel<boolean>('modelValue')

const mode = ref<'all' | 'skipped' | 'overridden'>('all')
const rows = ref<DifferenceRow[]>([])
const loading = ref(false)
const pager = ref({ total: 0, page: 1, page_size: 20 })

async function load() {
  loading.value = true
  try {
    const page = await differences(props.profileId, {
      type: mode.value,
      page: pager.value.page,
      page_size: pager.value.page_size,
    })
    rows.value = page.items
    pager.value.total = page.total
  } catch {
    rows.value = []
  } finally {
    loading.value = false
  }
}

function switchMode(m: 'all' | 'skipped' | 'overridden') {
  mode.value = m
  pager.value.page = 1
  load()
}

function pageChange(p: number) {
  pager.value.page = p
  load()
}

function reasonText(r: any): string {
  if (r.override) return '档案能力变量'
  const note = r.reason_note
  return note || r.reason_code || '-'
}

watch(() => props.profileId, () => {
  pager.value.page = 1
  load()
})
onMounted(load)
watch(() => visible.value, (open) => {
  if (open) {
    pager.value.page = 1
    load()
  }
})
</script>

<template>
  <el-dialog v-model="visible" title="差异清单" width="640px">
    <div class="diff-toolbar">
      <el-radio-group v-model="mode" size="small" @change="switchMode(mode)">
        <el-radio-button label="all">全部</el-radio-button>
        <el-radio-button label="skipped">只看跳过</el-radio-button>
        <el-radio-button label="overridden">只看档案能力变量</el-radio-button>
      </el-radio-group>
      <span class="count">共 {{ pager.total }} 项</span>
    </div>
    <el-table :data="rows" v-loading="loading" size="small" max-height="360">
      <el-table-column prop="target_type" label="类型" width="90" />
      <el-table-column prop="path" label="路径" min-width="240" />
      <el-table-column label="差异" min-width="160">
        <template #default="{ row }">
          <el-tag :type="row.override ? 'primary' : 'danger'" size="small">
            {{ reasonText(row) }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
    <div class="diff-pager">
      <el-pagination
        layout="prev, pager, next"
        :total="pager.total"
        :page-size="pager.page_size"
        :current-page="pager.page"
        @current-change="pageChange"
        size="small"
      />
    </div>
  </el-dialog>
</template>

<style scoped>
.diff-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.count {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.diff-pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
