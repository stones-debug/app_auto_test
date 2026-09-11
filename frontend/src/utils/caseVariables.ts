/**
 * 用例变量快捷展示与覆盖的共享口径（前端）。
 *
 * 与后端一致：只展示/编辑动作与断言参数中真实出现的 `${name}`。两条展示口径：
 * - 套件页编排项行：最多 2 项摘要，超出显示「+N 更多」，点击打开编排项覆盖面板；
 * - APP 档案页用例行：竖排展示全部变量，单个变量就地编辑，一个值写入其全部引用节点。
 */

export const MAX_VARIABLE_PREVIEW = 2

export interface VariableChip {
  name: string
  display_value: string
  source?: string
  status: string
  reference_count?: number
}

export interface EditorVariable {
  name: string
  status: string
  reference_count: number
  inherited_value: string | null
  inherited_scope: string | null
  /** 编排项级覆盖（套件编排项面板） */
  override_enabled?: boolean
  override_value?: string
}

export interface VariableOverrideUpdate {
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

export interface ProfileCaseVariableLike {
  name: string
  status: string
  reference_count: number
  inherited_value: string | null
  inherited_scope: string | null
  references: {
    node_key: string
    node_type: 'step' | 'assertion'
    override_enabled: boolean
    override_value: string
  }[]
}

/** 变量在一张用例内的覆盖状态：是否已覆盖、各节点覆盖值是否统一。 */
export function variableOverrideState(variable: ProfileCaseVariableLike): {
  overridden: boolean
  uniform: boolean
  value: string
} {
  const enabled = variable.references.filter((ref) => ref.override_enabled)
  const values = new Set(enabled.map((ref) => ref.override_value))
  return {
    overridden: enabled.length > 0,
    uniform: values.size <= 1,
    value: enabled.length > 0 ? enabled[0].override_value : (variable.inherited_value ?? ''),
  }
}

/** 表内展示文本：多节点覆盖值不一致时显示「多个值」，空值区分「（空）」与「未定义」。 */
export function variableDisplayText(variable: ProfileCaseVariableLike): string {
  const state = variableOverrideState(variable)
  if (state.overridden && !state.uniform) return '多个值'
  if (state.value === '') return variable.inherited_value === null && !state.overridden ? '未定义' : '（空）'
  return state.value
}

/** 进入编辑时的初始值：统一值直接带出，多值状态留空等待输入统一值。 */
export function variableEditSeed(variable: ProfileCaseVariableLike): string {
  const state = variableOverrideState(variable)
  if (state.overridden && !state.uniform) return ''
  return state.value
}

/**
 * occurrence 变量覆盖写入载荷：一个变量在该用例内被多少节点引用都只提交一条。
 * 引用节点仅用于展示/计数；``value=null`` 表示恢复当前 occurrence 的覆盖。
 */
export function buildVariableUpdates(
  variable: { name: string; references: { node_key: string; node_type: 'step' | 'assertion' }[] },
  value: string | null,
): VariableOverrideUpdate {
  return { name: variable.name, value }
}

/**
 * 就地编辑提交：值等于当前生效值时不产生变更（返回 null），避免空提交推进 revision。
 * 多值状态（各节点覆盖值不一致）下任何输入都是变更——包括统一为空串。
 */
export function variableQuickUpdates(
  variable: ProfileCaseVariableLike,
  value: string,
): VariableOverrideUpdate | null {
  const state = variableOverrideState(variable)
  if (state.overridden && !state.uniform) return buildVariableUpdates(variable, value)
  if (value === state.value) return null
  return buildVariableUpdates(variable, value)
}

/** 恢复原值：删除该变量在其全部引用节点上的覆盖，重新继承底层定义。 */
export function variableRestoreUpdates(variable: ProfileCaseVariableLike): VariableOverrideUpdate {
  return buildVariableUpdates(variable, null)
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
