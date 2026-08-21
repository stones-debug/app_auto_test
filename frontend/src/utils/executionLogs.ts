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

/**
 * 合并 REST 与 live 日志：
 * - 相同 (created_at,level,message) 的 WS/REST 条目只保留一条（按后端 log id 去重）；
 * - 按 (created_at, id) 排序，同毫秒日志次序稳定。
 */
export function mergeExecutionLogs(rest: LogLike[], live: LogLike[]): LogLike[] {
  const byKey = new Map<string, LogLike>()
  for (const l of [...rest, ...live]) {
    const key = `${String(l.created_at ?? '')}|${String(l.level ?? '')}|${String(l.message ?? '')}`
    if (!byKey.has(key)) byKey.set(key, l)
  }
  return Array.from(byKey.values()).sort(
    (a, b) =>
      String(a.created_at).localeCompare(String(b.created_at)) ||
      Number(a.id ?? 0) - Number(b.id ?? 0),
  )
}