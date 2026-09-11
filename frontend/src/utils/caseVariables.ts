/**
 * 用例变量快捷展示与覆盖的共享口径（前端）。
 *
 * 与后端一致：只展示/编辑动作与断言参数中真实出现的 `${name}`；
 * 行内最多 2 项，超出显示「+N 更多」；状态色随展示层统一。
 */

export const MAX_VARIABLE_PREVIEW = 2

export interface VariableChip {
  name: string
  display_value: string
  source?: string
  status: string
  reference_count?: number
}

export interface EditorReference {
  node_type: 'step' | 'assertion'
  node_key: string
  order: number | null
  node_name: string
  inherited_value: string | null
  override_enabled: boolean
  override_value: string
}

export interface EditorVariable {
  name: string
  status: string
  reference_count: number
  inherited_value: string | null
  inherited_scope: string | null
  /** occurrence 模式：编排项级覆盖 */
  override_enabled?: boolean
  override_value?: string
  /** nodes 模式：按引用节点分别覆盖 */
  references?: EditorReference[]
}

export interface VariableOverrideUpdate {
  node_type: 'step' | 'assertion'
  node_key: string
  name: string
  value: string | null
}

const VARIABLE_STATUS_META: Record<string, { label: string; tone: string }> = {
  overridden: { label: '已覆盖', tone: 'overridden' },
  inherited: { label: '继承', tone: 'inherited' },
  undefined: { label: '未定义', tone: 'undefined' },
  random: { label: '随机', tone: 'random' },
  mixed: { label: '多个值', tone: 'mixed' },
}

export function variableStatusMeta(status: string): { label: string; tone: string } {
  return VARIABLE_STATUS_META[status] ?? { label: status || '未知', tone: 'inherited' }
}

export function variableToken(name: string): string {
  return `\${${name}}`
}

/** 行内最多显示 2 项，其余折叠为「+N 更多」。 */
export function variablePreviewChips(
  preview: VariableChip[] | undefined,
  total: number,
): { visible: VariableChip[]; hidden: number } {
  const visible = (preview ?? []).slice(0, MAX_VARIABLE_PREVIEW)
  return { visible, hidden: Math.max(total - visible.length, 0) }
}

/** 编排项覆盖的增量提交：关闭开关=删除覆盖（null），空串是合法覆盖值。 */
export function buildOccurrenceUpdates(
  variables: EditorVariable[],
  state: Record<string, { enabled: boolean; value: string }>,
): Record<string, string | null> {
  const updates: Record<string, string | null> = {}
  for (const variable of variables) {
    const entry = state[variable.name]
    if (!entry || !entry.enabled) {
      if (variable.override_enabled) updates[variable.name] = null
      continue
    }
    if (!variable.override_enabled || variable.override_value !== entry.value) {
      updates[variable.name] = entry.value
    }
  }
  return updates
}

/** 节点级覆盖的增量提交：仅提交相对原值发生变化的项。 */
export function diffNodeEntries(
  original: EditorReference[],
  edited: { node_key: string; node_type: 'step' | 'assertion'; name: string; enabled: boolean; value: string }[],
): VariableOverrideUpdate[] {
  const updates: VariableOverrideUpdate[] = []
  for (const entry of edited) {
    const source = original.find((ref) => ref.node_key === entry.node_key)
    if (!entry.enabled) {
      if (source?.override_enabled) {
        updates.push({ node_type: entry.node_type, node_key: entry.node_key, name: entry.name, value: null })
      }
      continue
    }
    if (!source?.override_enabled || source.override_value !== entry.value) {
      updates.push({ node_type: entry.node_type, node_key: entry.node_key, name: entry.name, value: entry.value })
    }
  }
  return updates
}

/** 「全部设为同一值」：主体仍是多个节点级覆盖，只是取值相同。 */
export function applySameValue(
  references: EditorReference[],
  name: string,
  value: string,
): { node_key: string; node_type: 'step' | 'assertion'; name: string; enabled: boolean; value: string }[] {
  return references.map((ref) => ({
    node_key: ref.node_key,
    node_type: ref.node_type,
    name,
    enabled: true,
    value,
  }))
}

/** 变量区域点击必须阻断父级的拖拽/排序编辑/双击打开用例；只读时不打开面板。 */
export function handleVariableAreaClick(
  event: { stopPropagation: () => void },
  readonly: boolean,
  open: () => void,
): void {
  event.stopPropagation()
  if (readonly) return
  open()
}

export interface MembershipVariableLike {
  name: string
  display_value: string
  status: string
  reference_count: number
  inherited_scope: string | null
  override_enabled: boolean
}

/** 套件编排项详情 → 行内摘要（保存后只刷新当前编排项，不重置滚动/排序/展开）。 */
export function membershipVariablesToPreview(variables: MembershipVariableLike[]): VariableChip[] {
  return variables.slice(0, MAX_VARIABLE_PREVIEW).map((variable) => ({
    name: variable.name,
    display_value: variable.display_value,
    source: variable.override_enabled ? 'occurrence' : (variable.inherited_scope ?? variable.status),
    status: variable.status,
    reference_count: variable.reference_count,
  }))
}

export interface ProfileCaseVariableLike {
  name: string
  status: string
  reference_count: number
  inherited_value: string | null
  inherited_scope: string | null
  references: { node_key: string; override_enabled: boolean; override_value: string }[]
}

/** APP 档案变量详情 → 行内摘要；同名变量在不同节点取值不同时显示「多个值」。 */
export function profileVariablesToPreview(variables: ProfileCaseVariableLike[]): VariableChip[] {
  return variables.slice(0, MAX_VARIABLE_PREVIEW).map((variable) => {
    const enabled = variable.references.filter((ref) => ref.override_enabled)
    const display =
      variable.status === 'mixed'
        ? '多个值'
        : enabled.length
          ? enabled[0].override_value
          : (variable.inherited_value ?? '')
    return {
      name: variable.name,
      display_value: display,
      source: enabled.length ? 'occurrence' : (variable.inherited_scope ?? variable.status),
      status: variable.status,
      reference_count: variable.reference_count,
    }
  })
}
