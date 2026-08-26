// 方案 §7.2：把扁平排除项构造成 套件→用例→步骤 的独立层级树（报告 N/A 视图专用）。
// 结构：suite 容器（按 suite_id + 名称）→ case 容器（按 case_id + 名称）→ 叶子（step/assertion/suite_step）。
// 无 suite/case 上下文的历史数据以独立叶子兜底，保证永远可渲染。

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

  const segmentOf = (r: ReportExclusion) => r.path?.split('/') || []

  for (const row of rows) {
    const seg = segmentOf(row)
    const why = {
      reasonCode: row.reason_code,
      reasonNote: row.reason_note,
      sourceType: row.source_type,
      path: row.path,
    }

    if (row.target_type === 'suite') {
      roots.push({
        key: `ex-suite-${row.suite_id ?? ''}-${row.node_key ?? row.path}`,
        name: row.path || `套件 #${row.suite_id}`,
        isLeaf: true,
        children: [],
        targetType: row.target_type,
        ...why,
      })
      continue
    }
    if (row.target_type === 'case') {
      const suiteId = `${row.suite_id ?? ''}`
      const caseKey = `${suiteId}::${row.case_id ?? ''}`
      if (!caseNodes.has(caseKey)) {
        const caseName = seg[1] || `用例 #${row.case_id}`
        caseNodes.set(caseKey, { key: `case:${caseKey}`, name: caseName, isLeaf: false, children: [] })
      }
      caseNodes.get(caseKey)!.children.push({
        key: `ex-case-${row.case_id}-${row.node_key ?? row.path}`,
        name: row.path || `用例 #${row.case_id}`,
        isLeaf: true,
        children: [],
        targetType: row.target_type,
        ...why,
      })
      continue
    }

    const suiteId = `${row.suite_id ?? ''}`
    const caseId = `${row.case_id ?? ''}`
    const leaf: ExclusionTreeNode = {
      key: `ex-${row.target_type}-${suiteId}-${caseId}-${row.node_key ?? row.path}`,
      name: row.path || row.node_key || row.target_type,
      isLeaf: true,
      children: [],
      targetType: row.target_type,
      ...why,
    }

    if (caseId && caseId !== '') {
      const caseKey = `${suiteId}::${caseId}`
      if (!caseNodes.has(caseKey)) {
        const caseName = row.path.split('/')[1] || `用例 #${caseId}`
        caseNodes.set(caseKey, { key: `case:${caseKey}`, name: caseName, isLeaf: false, children: [] })
      }
      caseNodes.get(caseKey)!.children.push(leaf)
    } else if (suiteId && suiteId !== '') {
      if (!suiteNodes.has(suiteId)) {
        suiteNodes.set(suiteId, {
          key: `suite:${suiteId}`,
          name: row.path?.split('/')[0] || `套件 #${suiteId}`,
          isLeaf: false,
          children: [],
        })
      }
      suiteNodes.get(suiteId)!.children.push(leaf)
    } else {
      roots.push(leaf)
    }
  }

  // case 节点挂回对应 suite 下；无 suite 的 case 独立成根
  for (const [caseKey, caseNode] of caseNodes) {
    const suiteId = caseKey.split('::')[0]
    const suiteNode = suiteNodes.get(suiteId)
    if (suiteNode) suiteNode.children.push(caseNode)
    else roots.push(caseNode)
  }
  roots.push(...suiteNodes.values())
  return roots
}

/** 收集树中全部节点的 key（展开/收起用）。 */
export function flattenTreeKeys(nodes: ExclusionTreeNode[]): string[] {
  return nodes.flatMap((n) => (n.isLeaf ? [n.key] : [n.key, ...flattenTreeKeys(n.children)]))
}
