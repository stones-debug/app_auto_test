import type { SkipTarget } from '@/api/appProfiles'

export interface ProfileSkipNodeContext {
  node_type: string
  id?: number | null
  node_key?: string | null
  _suiteId?: number
  _caseId?: number
}

/** 将工作台展示节点转换为带套件上下文的精确跳过目标。 */
export function buildProfileSkipTarget(node: ProfileSkipNodeContext): SkipTarget | null {
  if (node.node_type === 'suite' && node.id != null) {
    return { type: 'suite', suite_id: node.id }
  }
  if (node.node_type === 'case' && node.id != null && node._suiteId != null) {
    return { type: 'case', suite_id: node._suiteId, case_id: node.id }
  }
  if (
    (node.node_type === 'step' || node.node_type === 'assertion')
    && node._suiteId != null
    && node._caseId != null
    && node.node_key
  ) {
    return {
      type: node.node_type,
      suite_id: node._suiteId,
      case_id: node._caseId,
      node_key: node.node_key,
    }
  }
  return null
}
