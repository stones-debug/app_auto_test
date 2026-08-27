<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { formatDateTime } from '@/utils/format'
import { executionConnectionState } from '@/utils/executionRealtime'

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
    terminal?: boolean
  }>(),
  { connected: true, connecting: false, terminal: false },
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

const connectionState = computed(() => (
  executionConnectionState(props.terminal, props.connected, props.connecting)
))

// 深色背景语义色：主题变量（--text/--text-2）是浅色背景定义的深色值，此处须用深底浅色
const LEVEL_COLOR: Record<string, string> = {
  DEBUG: '#94a3b8',
  INFO: '#e2e8f0',
  WARN: '#fbbf24',
  ERROR: '#f87171',
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
        <span class="dot" :class="connectionState.kind" />
        {{ connectionState.label }}
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
        <span class="log-time v2-aux">{{ formatDateTime(log.created_at) }}</span>
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
  flex: 1;
  min-height: 0;
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
.dot.done {
  background: #64748b;
}
.level-filter {
  width: 110px;
}
.log-body {
  flex: 1;
  min-height: 0;
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
  overflow-wrap: anywhere;
}
.log-time {
  color: #94a3b8;
  flex-shrink: 0;
  white-space: nowrap;
}
.log-level {
  width: 44px;
  flex-shrink: 0;
}
</style>
