/** 可按模块批量选择的用例最小数据结构。 */
export interface SelectableSuiteCase {
  id: number
}

export interface GroupSelectionState {
  checked: boolean
  indeterminate: boolean
}

/** 计算一个模块组的全选状态。空分组不应显示为已全选。 */
export function getGroupSelectionState(
  selectedIds: ReadonlySet<number>,
  cases: readonly SelectableSuiteCase[],
): GroupSelectionState {
  const selectedCount = cases.filter((item) => selectedIds.has(item.id)).length
  return {
    checked: cases.length > 0 && selectedCount === cases.length,
    indeterminate: selectedCount > 0 && selectedCount < cases.length,
  }
}

/** 只增删当前可见模块组内的用例，保留其它模块及搜索结果外的选择。 */
export function setGroupSelection(
  selectedIds: ReadonlySet<number>,
  cases: readonly SelectableSuiteCase[],
  selected: boolean,
): Set<number> {
  const next = new Set(selectedIds)
  for (const item of cases) {
    if (selected) next.add(item.id)
    else next.delete(item.id)
  }
  return next
}

/** 切换一个模块的展开状态，不影响该模块的已选用例。 */
export function toggleCollapsedGroup(
  collapsedGroups: ReadonlySet<string>,
  groupName: string,
): Set<string> {
  const next = new Set(collapsedGroups)
  if (next.has(groupName)) next.delete(groupName)
  else next.add(groupName)
  return next
}
