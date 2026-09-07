// 方案 §7.2：把扁平排除项构造成 套件→用例→步骤 的独立层级树（报告 N/A 视图专用）。
// 只要排除项携带 suite_id，就主动补出套件容器，避免只有 case/step 排除时层级断裂。

import type { ReportExclusion } from '@/api/reports'

export interface ExclusionTreeNode {
  key: string
  name: string
  isLeaf: boolean
  children: ExclusionTreeNode[]
  targetType?: string
  reasonCode?: string
  reasonNote?: string | null
  sourceType?: string | null
  path?: string
}

export function buildExclusionTree(exclusions: ReportExclusion[]): ExclusionTreeNode[] {
  const rows = exclusions ?? []
  if (!rows.length) return []

  const suiteNodes = new Map<string, ExclusionTreeNode>()
  const caseNodes = new Map<string, ExclusionTreeNode>()
  const roots: ExclusionTreeNode[] = []

  const segmentsOf = (row: ReportExclusion) =>
    (row.path ?? '').split('/').map((part) => part.trim()).filter(Boolean)

  const ensureSuite = (row: ReportExclusion): ExclusionTreeNode | null => {
    if (row.suite_id == null) return null
    const suiteId = String(row.suite_id)
    if (!suiteNodes.has(suiteId)) {
      suiteNodes.set(suiteId, {
        key: `suite:${suiteId}`,
        name: segmentsOf(row)[0] || `套件 #${suiteId}`,
        isLeaf: false,
        children: [],
      })
    }
    return suiteNodes.get(suiteId)!
  }

  const ensureCase = (row: ReportExclusion): ExclusionTreeNode | null => {
    if (row.case_id == null) return null
    const suiteId = row.suite_id == null ? '' : String(row.suite_id)
    const caseId = String(row.case_id)
    const occurrence = row.occurrence_order == null ? '' : `::${row.occurrence_order}`
    const caseKey = `${suiteId}::${caseId}${occurrence}`
    if (!caseNodes.has(caseKey)) {
      const segments = segmentsOf(row)
      const caseNode: ExclusionTreeNode = {
        key: `case:${caseKey}`,
        name: segments[row.suite_id == null ? 0 : 1] || `用例 #${caseId}`,
        isLeaf: false,
        children: [],
      }
      caseNodes.set(caseKey, caseNode)
      const suiteNode = ensureSuite(row)
      if (suiteNode) suiteNode.children.push(caseNode)
      else roots.push(caseNode)
    }
    return caseNodes.get(caseKey)!
  }

  for (const row of rows) {
    const why = {
      reasonCode: row.reason_code,
      reasonNote: row.reason_note,
      sourceType: row.source_type,
      path: row.path,
    }
    const leaf: ExclusionTreeNode = {
      key: `ex-${row.target_type}-${row.suite_id ?? ''}-${row.case_id ?? ''}-${row.occurrence_order ?? ''}-${row.node_key ?? row.path}`,
      name: row.path || row.node_key || row.target_type,
      isLeaf: true,
      children: [],
      targetType: row.target_type,
      ...why,
    }

    if (row.target_type === 'suite') {
      const suiteNode = ensureSuite(row)
      if (suiteNode) suiteNode.children.push(leaf)
      else roots.push(leaf)
      continue
    }
    if (row.target_type === 'case' || row.case_id != null) {
      const caseNode = ensureCase(row)
      if (caseNode) caseNode.children.push(leaf)
      else roots.push(leaf)
      continue
    }
    const suiteNode = ensureSuite(row)
    if (suiteNode) suiteNode.children.push(leaf)
    else roots.push(leaf)
  }

  roots.push(...suiteNodes.values())
  return roots
}

/** 收集树中全部节点的 key（展开/收起用）。 */
export function flattenTreeKeys(nodes: ExclusionTreeNode[]): string[] {
  return nodes.flatMap((node) => (
    node.isLeaf ? [node.key] : [node.key, ...flattenTreeKeys(node.children)]
  ))
}
