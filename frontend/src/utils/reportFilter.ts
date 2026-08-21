// Step 7：报告详情折叠状态工具——切换「只看失败/异常」时保留仍可见的展开项。
export interface FilterableCase {
  id: number
  status: string
}

/**
 * onlyFailed 切换后的目标展开集合：
 * - 切到 onlyFailed：保留仍可见的展开项，并默认展开 failed/error 用例；
 * - 切回全部：保留仍可见（全部）的展开项。
 */
export function applyOnlyFailed(
  active: number[],
  onlyFailed: boolean,
  allCases: FilterableCase[],
): number[] {
  const visible = onlyFailed
    ? allCases.filter((c) => ['failed', 'error'].includes(c.status))
    : allCases
  const visibleIds = new Set(visible.map((c) => c.id))
  let next = active.filter((id) => visibleIds.has(id))
  if (onlyFailed) {
    const failedIds = visible.filter((c) => ['failed', 'error'].includes(c.status)).map((c) => c.id)
    next = [...new Set([...next, ...failedIds])]
  }
  return next
}