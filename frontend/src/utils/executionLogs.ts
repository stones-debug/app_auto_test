// Step 7：执行详情日志合并工具——REST（后端 log id）与 WS live 日志去重排序。
export interface LogLike {
  id?: number
  level: string
  message: string
  source?: string
  created_at?: string
}

export function liveLogKey(level: string, message: string, timestamp: string | undefined): string {
  return `live:${String(timestamp ?? '')}|${String(level ?? '')}|${String(message ?? '')}`
}

/** 解析时间戳为数值（兼容 +00:00 / Z / naive 格式；无效返回 0）。 */
function timeValue(t: string | undefined): number {
  const n = Date.parse(String(t ?? ''))
  return Number.isNaN(n) ? 0 : n
}

/** live 行与任一 REST 行「level+message 完全相同 且 时间差绝对值 ≤1000ms」→ 视为同一行。 */
function matchesRestRow(l: LogLike, rest: LogLike[]): boolean {
  const lt = timeValue(l.created_at)
  for (const r of rest) {
    if (String(r.level) !== String(l.level)) continue
    if (String(r.message) !== String(l.message)) continue
    if (Math.abs(timeValue(r.created_at) - lt) <= 1000) return true
  }
  return false
}

/** 按时间数值升序；时间相同按 id 数值序（live 负 id 自然排前）。 */
function cmpLog(a: LogLike, b: LogLike): number {
  return timeValue(a.created_at) - timeValue(b.created_at) || Number(a.id ?? 0) - Number(b.id ?? 0)
}

/**
 * 合并 REST 与 live 日志：
 * - REST 行按 id 去重（保留后出现的）；
 * - live 行若与任一 REST 行「level+message 完全相同 且 时间差 ≤1000ms」视为同一行，丢弃 live 版
 *   （规避 WS 广播 timestamp 与 REST 落库 created_at 微秒级不一致导致的重复）；
 * - 其余 live 行保留，live 行之间按 (created_at, level, message) 键去重；
 * - 按时间数值升序，相同时按 id 数值序。
 */
export function mergeExecutionLogs(rest: LogLike[], live: LogLike[]): LogLike[] {
  // REST 行按 id 去重（保留后出现的）
  const restById = new Map<number, LogLike>()
  for (const l of rest) {
    const id = Number(l.id ?? 0)
    restById.set(id, l)
  }
  const restRows = Array.from(restById.values())

  // live 行：命中 REST 容差行则丢弃；其余保留并按 (created_at, level, message) 去重
  const liveByKey = new Map<string, LogLike>()
  for (const l of live) {
    if (matchesRestRow(l, restRows)) continue
    const key = `${String(l.created_at ?? '')}|${String(l.level ?? '')}|${String(l.message ?? '')}`
    if (!liveByKey.has(key)) liveByKey.set(key, l)
  }

  return [...restRows, ...Array.from(liveByKey.values())].sort(cmpLog)
}

/**
 * 增量合并：把新一批 REST 日志（batch）并入已显示日志（existing，REST+live 混合）。
 * 复用 mergeExecutionLogs 的规则：batch 与 existing 中的 REST 行按 id 去重（batch 在后，胜出），
 * existing 中命中 batch REST 容差行的 live 行会被丢弃。最终返回排序后的数组。
 */
export function appendLogs(existing: LogLike[], batch: LogLike[]): LogLike[] {
  const existingRest: LogLike[] = []
  const existingLive: LogLike[] = []
  for (const l of existing) {
    if ((l.id ?? 0) > 0) existingRest.push(l)
    else existingLive.push(l)
  }
  return mergeExecutionLogs([...existingRest, ...batch], existingLive)
}

/**
 * 返回传给 after_timestamp 的游标：items 中时间值最大的行的原始 created_at 字符串；
 * 空数组返回 null。
 */
export function nextLogCursor(items: LogLike[]): string | null {
  if (!items.length) return null
  let max: LogLike | null = null
  let maxT = -Infinity
  for (const l of items) {
    const t = timeValue(l.created_at)
    if (t > maxT) {
      maxT = t
      max = l
    }
  }
  return max ? String(max.created_at ?? '') : null
}
