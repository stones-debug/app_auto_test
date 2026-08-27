export const REPORT_LOG_PAGE_SIZE = 200

/** 报告日志优先展示最新一页；继续加载时逐页向前扩展。 */
export function visibleReportLogs<T>(logs: T[], limit: number): T[] {
  if (limit <= 0) return []
  return logs.slice(Math.max(0, logs.length - limit))
}
