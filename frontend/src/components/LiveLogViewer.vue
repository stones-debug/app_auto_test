<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

// V2 §5.14：实时日志查看器——增量日志、级别过滤、自动滚动、连接状态。
export interface LogEntry {
  id?: number
  level: string
  message: string
  source?: string
  created_at?: string
}

const props = withDefaults(
  defineProps<{
    logs: LogEntry[]
    connected?: boolean
    connecting?: boolean
  }>(),
  { connected: true, connecting: false },
)

const LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARN', 'ERROR']
const levelFilter = ref('ALL')
const container = ref<HTMLElement | null>(null)
const follow = ref(true)
const newCount = ref(0)
let lastHeight = 0

const filtered = computed(() => {
  if (levelFilter.value === 'ALL') return props.logs
  return props.logs.filter((l) => l.level === levelFilter.value)
})

const LEVEL_COLOR: Record<string, string> = {
  DEBUG: 'var(--text-2)',
  INFO: 'var(--text)',
  WARN: 'var(--warning)',
  ERROR: 'var(--danger)',
}

watch(
  () => props.logs.length,
  async () => {
    await nextTick()
    if (!container.value) return
    const el = container.value
    if (follow.value) {
      el.scrollTop = el.scrollHeight
      newCount.value = 0
    } else if (el.scrollHeight > lastHeight) {
      newCount.value += 1
    }
    lastHeight = el.scrollHeight
  },
)

function onScroll() {
  if (!container.value) return
  const el = container.value
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  follow.value = nearBottom
  if (nearBottom) newCount.value = 0
}

function scrollToBottom() {
  follow.value = true
  newCount.value = 0
  if (container.value) container.value.scrollTop = container.value.scrollHeight
}
</script>

<template>
  <div class="live-log">
    <div class="log-toolbar">
      <span class="conn-state">
        <span class="dot" :class="connected ? 'ok' : connecting ? 'pending' : 'down'" />
        {{ connected ? '已连接' : connecting ? '连接中…' : '已断开' }}
      </span>
      <el-select v-model="levelFilter" size="small" class="level-filter">
        <el-option v-for="l in LEVELS" :key="l" :label="l" :value="l" />
      </el-select>
      <el-button v-if="newCount" size="small" type="primary" text @click="scrollToBottom">
        有 {{ newCount }} 条新日志
      </el-button>
    </div>
    <div ref="container" class="log-body" @scroll="onScroll">
      <div v-for="(log, i) in filtered" :key="log.id ?? i" class="log-line" :style="{ color: LEVEL_COLOR[log.level] ?? 'var(--text)' }">
        <span class="log-time v2-aux">{{ log.created_at ? new Date(log.created_at).toLocaleTimeString() : '' }}</span>
        <span class="log-level">{{ log.level }}</span>
        <span class="log-msg">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.live-log {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 240px;
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  overflow: hidden;
  background: #0f172a;
}
.log-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: #1e293b;
  color: #cbd5e1;
  font-size: 12px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  margin-right: 4px;
}
.dot.ok {
  background: #10b981;
}
.dot.pending {
  background: #f59e0b;
}
.dot.down {
  background: #ef4444;
}
.level-filter {
  width: 110px;
}
.log-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}
.log-line {
  display: flex;
  gap: 8px;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-time {
  color: #64748b;
}
.log-level {
  width: 44px;
  flex-shrink: 0;
}
</style>