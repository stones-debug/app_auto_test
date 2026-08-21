<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

import StatusBadge from '@/components/StatusBadge.vue'
import StatCard from '@/components/StatCard.vue'
import { getDashboardOverview } from '@/api/dashboard'
import { useProjectContextStore } from '@/stores/projectContext'
import { usePermission } from '@/composables/usePermission'

const route = useRoute()
const router = useRouter()
const ctx = useProjectContextStore()
const { canWriteAssets, canExecute } = usePermission()

const projectId = computed(() => Number(route.params.projectId))
const loading = ref(false)
const overview = ref<Awaited<ReturnType<typeof getDashboardOverview>> | null>(null)
let trendChart: echarts.ECharts | null = null
let trendEl: HTMLElement | null = null

async function load() {
  loading.value = true
  try {
    await ctx.load(projectId.value, { force: true })
    overview.value = await getDashboardOverview({ project_id: projectId.value, range: '30d' })
    renderChart()
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!trendEl || !overview.value) return
  trendChart ??= echarts.init(trendEl)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['通过', '失败', '异常'] },
    grid: { left: 40, right: 16, top: 32, bottom: 24 },
    xAxis: { type: 'category', data: overview.value.trend.map((t) => t.date.slice(5)) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      { name: '通过', type: 'line', smooth: true, data: overview.value.trend.map((t) => t.passed), itemStyle: { color: '#10b981' } },
      { name: '失败', type: 'line', smooth: true, data: overview.value.trend.map((t) => t.failed), itemStyle: { color: '#dc2626' } },
      { name: '异常', type: 'line', smooth: true, data: overview.value.trend.map((t) => t.error), itemStyle: { color: '#f59e0b' } },
    ],
  })
}

function handleResize() {
  trendChart?.resize()
}

onMounted(() => {
  load()
  window.addEventListener('resize', handleResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
})
</script>

<template>
  <div v-loading="loading">
    <div class="head-card">
      <div class="v2-page-title">{{ ctx.project?.name ?? '项目概览' }}</div>
      <div class="v2-aux">{{ ctx.project?.description || '暂无描述' }}</div>
      <div class="head-tags">
        <el-tag :type="ctx.project?.visibility === 'public' ? 'success' : 'info'" size="small">
          {{ ctx.project?.visibility === 'public' ? '公开' : '私有' }}
        </el-tag>
        <el-tag v-if="ctx.role" size="small" type="warning">{{ ctx.role }}</el-tag>
      </div>
    </div>

    <template v-if="overview">
      <div class="kpi-row">
        <StatCard label="用例" :value="ctx.project?.case_count ?? 0" @click="router.push(`/projects/${projectId}/cases`)" />
        <StatCard label="套件" :value="ctx.project?.suite_count ?? 0" @click="router.push(`/projects/${projectId}/suites`)" />
        <StatCard label="元素" :value="ctx.project?.element_count ?? 0" @click="router.push(`/projects/${projectId}/elements`)" />
        <StatCard label="成功率" :value="overview.stats.success_rate > 0 ? overview.stats.success_rate.toFixed(1) + '%' : '—'" tone="success" @click="router.push('/reports')" />
      </div>

      <div class="chart-card">
        <div class="v2-card-title">执行趋势（近 30 天）</div>
        <div ref="trendEl" class="chart" />
      </div>

      <div class="content-card">
        <div class="v2-card-title">最近执行</div>
        <el-empty v-if="!overview.recent_executions.length" description="暂无执行记录" :image-size="60" />
        <div v-else>
          <div v-for="e in overview.recent_executions" :key="e.id" class="recent-item" @click="router.push(`/executions/${e.id}`)">
            <span class="recent-id">#{{ e.id }}</span>
            <StatusBadge :status="e.status" />
            <span class="spacer" />
            <el-button size="small" text>查看</el-button>
          </div>
        </div>
      </div>

      <div class="quick-row">
        <el-button v-if="canWriteAssets" type="primary" @click="router.push(`/projects/${projectId}/cases/new`)">新建用例</el-button>
        <el-button v-if="canExecute" @click="router.push(`/projects/${projectId}/suites`)">运行套件</el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.head-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 20px 24px;
  margin-bottom: 16px;
}
.head-tags {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.chart-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 16px;
  margin-bottom: 16px;
}
.chart {
  height: 260px;
  margin-top: 8px;
}
.recent-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.recent-id {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  color: var(--text-2);
}
.spacer {
  flex: 1;
}
.quick-row {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}
</style>