<script setup lang="ts">
import { onBeforeUnmount, onMounted, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

import StatusBadge from '@/components/StatusBadge.vue'
import StatCard from '@/components/StatCard.vue'
import { getDashboardOverview, type DashboardOverview } from '@/api/dashboard'
import { executionStatusMeta } from '@/api/executions'

const router = useRouter()
const loading = ref(false)
const data = ref<DashboardOverview | null>(null)
const range = ref<'7d' | '30d' | '90d'>('7d')

let trendChart: echarts.ECharts | null = null
let donutChart: echarts.ECharts | null = null
let trendEl: HTMLElement | null = null
let donutEl: HTMLElement | null = null

async function load() {
  loading.value = true
  try {
    data.value = await getDashboardOverview({ range: range.value })
    // 图表容器在 v-if="data" 内，首次赋值后需等 DOM 更新再初始化 ECharts
    await nextTick()
    renderCharts()
  } finally {
    loading.value = false
  }
}

function renderCharts() {
  if (!data.value) return
  renderTrend()
  renderDonut()
}

function renderTrend() {
  if (!trendEl) return
  trendChart ??= echarts.init(trendEl)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['通过', '失败', '异常'] },
    grid: { left: 40, right: 16, top: 32, bottom: 24 },
    xAxis: { type: 'category', data: data.value!.trend.map((t) => t.date.slice(5)) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      { name: '通过', type: 'line', smooth: true, data: data.value!.trend.map((t) => t.passed), itemStyle: { color: '#10b981' } },
      { name: '失败', type: 'line', smooth: true, data: data.value!.trend.map((t) => t.failed), itemStyle: { color: '#dc2626' } },
      { name: '异常', type: 'line', smooth: true, data: data.value!.trend.map((t) => t.error), itemStyle: { color: '#f59e0b' } },
    ],
  })
}

function renderDonut() {
  if (!donutEl) return
  donutChart ??= echarts.init(donutEl)
  const counts = data.value!.status_counts
  const map: Record<string, { name: string; color: string }> = {
    passed: { name: '通过', color: '#10b981' },
    failed: { name: '失败', color: '#dc2626' },
    error: { name: '异常', color: '#b91c1c' },
    running: { name: '运行中', color: '#2563eb' },
    stopping: { name: '停止中', color: '#d97706' },
    queued: { name: '排队中', color: '#64748b' },
    stopped: { name: '已停止', color: '#94a3b8' },
    cancelled: { name: '已取消', color: '#cbd5e1' },
  }
  const keys = ['passed', 'failed', 'error', 'running', 'stopping', 'queued', 'stopped', 'cancelled']
  const pieData = keys
    .filter((k) => (counts[k] ?? 0) > 0)
    .map((k) => ({ name: map[k].name, value: counts[k] ?? 0, itemStyle: { color: map[k].color } }))
  donutChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [
      {
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '45%'],
        data: pieData,
        label: { show: false },
      },
    ],
  })
}

function handleResize() {
  trendChart?.resize()
  donutChart?.resize()
}

const FOCUS = [
  { label: '活跃执行', key: 'active_execution_count', path: '/executions', tone: 'primary' as const },
  { label: '成功率', key: 'success_rate', path: '/reports', tone: 'success' as const, suffix: '%' },
  { label: '空闲设备/全部', key: 'available_device_count', path: '/devices', tone: 'warning' as const },
  { label: '我的项目', key: 'project_count', path: '/projects', tone: 'primary' as const },
]

function kpiValue(key: string, suffix = ''): string {
  const v = data.value?.stats
  if (!v) return '—'
  const raw = (v as unknown as Record<string, number>)[key]
  if (raw === undefined) return '—'
  if (key === 'available_device_count') {
    return `${v.available_device_count}/${v.device_count}`
  }
  if (key === 'success_rate') {
    return v.success_rate > 0 ? `${v.success_rate.toFixed(1)}${suffix}` : '—'
  }
  return String(raw)
}

function recentStatus(status: string) {
  return executionStatusMeta(status).label
}

function openExecution(id: number) {
  router.push(`/executions/${id}`)
}

watch(range, load)
onMounted(() => {
  load()
  window.addEventListener('resize', handleResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
  donutChart?.dispose()
})
</script>

<template>
  <div v-loading="loading">
    <div class="dash-head">
      <div class="v2-page-title">工作台</div>
      <div class="range-tabs">
        <el-radio-group v-model="range" size="small">
          <el-radio-button value="7d">7 天</el-radio-button>
          <el-radio-button value="30d">30 天</el-radio-button>
          <el-radio-button value="90d">90 天</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </div>

    <template v-if="data">
      <div class="kpi-row">
        <StatCard
          v-for="f in FOCUS"
          :key="f.key"
          :label="f.label"
          :value="kpiValue(f.key, f.suffix)"
          :tone="f.tone"
          @click="router.push(f.path)"
        />
      </div>

      <div class="charts-row">
        <div class="chart-card">
          <div class="v2-card-title">执行趋势</div>
          <div ref="trendEl" class="chart" />
        </div>
        <div class="chart-card">
          <div class="v2-card-title">状态分布</div>
          <div ref="donutEl" class="chart" />
        </div>
      </div>

      <div class="grid-row">
        <div class="content-card">
          <div class="v2-card-title">活跃与最近执行</div>
          <el-empty v-if="!data.recent_executions.length" description="暂无执行记录" :image-size="60" />
          <div v-else class="recent-list">
            <div v-for="e in data.recent_executions" :key="e.id" class="recent-item" @click="openExecution(e.id)">
              <span class="recent-id">#{{ e.id }}</span>
              <StatusBadge :status="e.status" />
              <span class="v2-aux">{{ recentStatus(e.status) }}</span>
              <span class="spacer" />
              <el-button size="small" text>查看</el-button>
            </div>
          </div>
          <el-button v-if="data.recent_executions.length" size="small" text type="primary" class="view-all" @click="router.push('/executions')">
            查看全部
          </el-button>
        </div>

        <div class="content-card">
          <div class="v2-card-title">快捷开始</div>
          <div class="quick-actions">
            <el-button type="primary" @click="router.push('/projects')">新建项目</el-button>
            <el-button @click="router.push('/devices')">查看设备</el-button>
          </div>
        </div>
      </div>
    </template>
    <el-empty v-else-if="!loading" description="暂无数据" />
  </div>
</template>

<style scoped>
.dash-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.range-tabs {
  display: flex;
  gap: 8px;
  align-items: center;
}
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.charts-row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}
.chart-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 16px;
}
.chart {
  height: 260px;
  margin-top: 8px;
}
.grid-row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
}
.recent-list {
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
.recent-item:hover {
  background: var(--primary-light);
}
.recent-id {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  color: var(--text-2);
}
.spacer {
  flex: 1;
}
.view-all {
  margin-top: 8px;
}
.quick-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>