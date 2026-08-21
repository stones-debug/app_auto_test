<script setup lang="ts" generic="T extends object">
// V2 §3.5：DataTable —— 加载态 + 空状态 + 分页 + 固定操作列插槽。
// 用法：<DataTable v-model:page="page" :total="total" :loading="loading" :data="rows" @refresh="load">
//   <el-table-column .../>
// </DataTable>
withDefaults(
  defineProps<{
    data: T[]
    total: number
    loading?: boolean
    page?: number
    pageSize?: number
    emptyText?: string
  }>(),
  { loading: false, page: 1, pageSize: 20, emptyText: '暂无数据' },
)

const emit = defineEmits<{
  'update:page': [value: number]
  'update:pageSize': [value: number]
  refresh: []
}>()

function onPageChange(p: number) {
  emit('update:page', p)
  emit('refresh')
}

function onSizeChange(s: number) {
  emit('update:pageSize', s)
  emit('update:page', 1)
  emit('refresh')
}
</script>

<template>
  <div>
    <el-table v-loading="loading" :data="data" stripe>
      <slot />
      <template #empty>
        <div class="empty-state">{{ emptyText }}</div>
      </template>
    </el-table>
    <el-pagination
      class="pager"
      :current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, sizes, prev, pager, next"
      :page-sizes="[20, 50, 100]"
      @update:current-page="onPageChange"
      @update:page-size="onSizeChange"
    />
  </div>
</template>