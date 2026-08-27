// 智能元素定位（Smart Locator）——纯函数工具层：类型、常量、校验、摘要、状态帮助器 + payload 组装。
// 全部逻辑可独立单测；组件只需做薄绑定，不复制校验/序列化逻辑。

export type SmartConditionAttribute =
  | 'text'
  | 'content_desc'
  | 'resource_id'
  | 'class_name'
  | 'package'
  | 'clickable'
  | 'enabled'
  | 'selected'
  | 'displayed'

export type SmartOperator = 'equals' | 'contains' | 'starts_with' | 'ends_with' | 'regex'

export interface SmartCondition {
  attribute: SmartConditionAttribute
  operator: SmartOperator
  value: string | boolean
}

export type SmartAxis = 'parent' | 'ancestor' | 'child' | 'descendant' | 'following_sibling' | 'preceding_sibling'

export interface SmartPathSegment {
  axis: SmartAxis
  depth?: number
}

export interface SmartAlternative {
  anchor?: SmartCondition[]
  path?: SmartPathSegment[]
  target: SmartCondition[]
}

export type SmartSearchDirection = 'up' | 'down'

export interface SmartSearch {
  scroll: boolean
  direction: SmartSearchDirection
  max_swipes: number
  duration_ms: number
  settle_ms: number
}

export type SmartSelectionPolicy = 'unique' | 'index'

export interface SmartSelection {
  policy: SmartSelectionPolicy
  index?: number
}

export interface SmartLocatorConfig {
  version: 1
  alternatives: SmartAlternative[]
  search: SmartSearch
  selection: SmartSelection
}

export type SmartConditionScope = 'target' | 'anchor'

// ---------------------------------------------------------------------------
// 常量（中文标签 + 布尔属性分组 + 轴是否需要 depth）
// ---------------------------------------------------------------------------

export interface SmartAttributeMeta {
  value: SmartConditionAttribute
  label: string
  boolean: boolean
}

export const SMART_ATTRIBUTES: SmartAttributeMeta[] = [
  { value: 'text', label: '文字', boolean: false },
  { value: 'content_desc', label: '内容描述', boolean: false },
  { value: 'resource_id', label: '资源 ID', boolean: false },
  { value: 'class_name', label: '类名', boolean: false },
  { value: 'package', label: '包名', boolean: false },
  { value: 'clickable', label: '可点击', boolean: true },
  { value: 'enabled', label: '可用', boolean: true },
  { value: 'selected', label: '选中', boolean: true },
  { value: 'displayed', label: '显示中', boolean: true },
]

export const SMART_OPERATORS: { value: SmartOperator; label: string }[] = [
  { value: 'equals', label: '等于' },
  { value: 'contains', label: '包含' },
  { value: 'starts_with', label: '以…开头' },
  { value: 'ends_with', label: '以…结尾' },
  { value: 'regex', label: '匹配正则' },
]

export const SMART_AXES: { value: SmartAxis; label: string; depthRequired: boolean }[] = [
  { value: 'parent', label: '父节点', depthRequired: true },
  { value: 'ancestor', label: '祖先', depthRequired: true },
  { value: 'child', label: '子节点', depthRequired: false },
  { value: 'descendant', label: '后代', depthRequired: false },
  { value: 'following_sibling', label: '后续兄弟', depthRequired: false },
  { value: 'preceding_sibling', label: '前序兄弟', depthRequired: false },
]

// 上限（与后端一致，前端必须先拦截）
export const SMART_LIMITS = {
  alternativesMax: 10,
  conditionsMax: 20,
  pathMax: 3,
  depthMax: 5,
  valueMaxLen: 200,
  maxSwipesMax: 20,
  durationMaxMs: 2000,
  durationMinMs: 100,
  settleMaxMs: 2000,
}

const ATTRIBUTE_VALUES = SMART_ATTRIBUTES.map((a) => a.value)
const OPERATOR_VALUES = SMART_OPERATORS.map((o) => o.value)
const AXIS_VALUES = SMART_AXES.map((a) => a.value)
const BOOLEAN_ATTRIBUTES = SMART_ATTRIBUTES.filter((a) => a.boolean).map((a) => a.value)

export function isBooleanAttribute(attr: string): boolean {
  return BOOLEAN_ATTRIBUTES.includes(attr as SmartConditionAttribute)
}

export function attributeLabel(attr: string): string {
  return SMART_ATTRIBUTES.find((a) => a.value === attr)?.label ?? attr
}

export function operatorLabel(op: string): string {
  return SMART_OPERATORS.find((o) => o.value === op)?.label ?? op
}

export function axisLabel(axis: string): string {
  return SMART_AXES.find((a) => a.value === axis)?.label ?? axis
}

export function axisRequiresDepth(axis: string): boolean {
  return SMART_AXES.find((a) => a.value === axis)?.depthRequired ?? false
}

export function defaultCondition(): SmartCondition {
  return { attribute: 'text', operator: 'equals', value: '' }
}

export function defaultPathSegment(): SmartPathSegment {
  return { axis: 'child' }
}

export function createDefaultConfig(): SmartLocatorConfig {
  return {
    version: 1,
    alternatives: [{
      anchor: [],
      path: [],
      target: [defaultCondition()],
    }],
    search: { scroll: true, direction: 'up', max_swipes: 8, duration_ms: 500, settle_ms: 300 },
    selection: { policy: 'unique' },
  }
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------

/** JSON 深拷贝（配置为纯 JSON 数据，可直接序列化往返）。 */
export function cloneSmartConfig(config: SmartLocatorConfig | null | undefined): SmartLocatorConfig | null {
  if (!config) return null
  return JSON.parse(JSON.stringify(config)) as SmartLocatorConfig
}

/** 组装提交 payload：smart → value 为 null、带序列化 config；普通类型 → config 为 null、带 value。 */
export function buildLocatorPayload(
  type: string,
  value: string,
  config: SmartLocatorConfig | null,
): { locator_type: string; locator_value: string | null; locator_config: SmartLocatorConfig | null } {
  if (type === 'smart') {
    return { locator_type: type, locator_value: null, locator_config: toSerializableConfig(config) }
  }
  return { locator_type: type, locator_value: value ?? '', locator_config: null }
}

/** 序列化为后端 JSON 契约：剔除空的 anchor/path，避免空数组触发「1..N」校验。 */
export function toSerializableConfig(config: SmartLocatorConfig | null): SmartLocatorConfig | null {
  if (!config) return null
  const alternatives: SmartAlternative[] = config.alternatives.map((alt) => {
    const out: SmartAlternative = { target: alt.target }
    if (alt.anchor && alt.anchor.length) out.anchor = alt.anchor
    if (alt.path && alt.path.length) out.path = alt.path
    return out
  })
  return { version: config.version, alternatives, search: config.search, selection: config.selection }
}

// ---------------------------------------------------------------------------
// 校验
// ---------------------------------------------------------------------------

export function validateSmartConfig(config: SmartLocatorConfig | null | undefined): string[] {
  const errors: string[] = []
  if (!config) return ['定位配置为空']

  if (config.version !== 1) {
    errors.push(`配置版本不支持：${String(config.version)}`)
    return errors
  }

  const alts = config.alternatives
  if (!Array.isArray(alts) || alts.length < 1 || alts.length > SMART_LIMITS.alternativesMax) {
    errors.push(`候选规则须为 1..${SMART_LIMITS.alternativesMax} 条，当前 ${alts?.length ?? 0}`)
  } else {
    alts.forEach((alt, ai) => {
      const label = `第 ${ai + 1} 个候选`
      if (alt.anchor && alt.anchor.length) validateConditions(alt.anchor, `${label}的锚点`, errors)
      if (alt.path && alt.path.length) validatePath(alt.path, `${label}的相对路径`, errors)
      if (!alt.target) {
        errors.push(`${label}缺少匹配条件`)
      } else if (!alt.target.length) {
        errors.push(`${label}的匹配条件不能为空`)
      } else {
        validateConditions(alt.target, `${label}的匹配条件`, errors)
      }
    })
  }

  const search = config.search
  if (!search) {
    errors.push('缺少滚动配置')
  } else {
    if (typeof search.scroll !== 'boolean') errors.push('滚动开关须为布尔值')
    if (search.direction !== 'up' && search.direction !== 'down') errors.push('滑动方向须为向上或向下')
    if (!Number.isInteger(search.max_swipes) || search.max_swipes < 1 || search.max_swipes > SMART_LIMITS.maxSwipesMax) {
      errors.push(`最大滑动次数须为 1..${SMART_LIMITS.maxSwipesMax}`)
    }
    if (!Number.isInteger(search.duration_ms) || search.duration_ms < SMART_LIMITS.durationMinMs || search.duration_ms > SMART_LIMITS.durationMaxMs) {
      errors.push(`滑动时长须为 ${SMART_LIMITS.durationMinMs}..${SMART_LIMITS.durationMaxMs}ms`)
    }
    if (!Number.isInteger(search.settle_ms) || search.settle_ms < 0 || search.settle_ms > SMART_LIMITS.settleMaxMs) {
      errors.push(`稳定等待须为 0..${SMART_LIMITS.settleMaxMs}ms`)
    }
  }

  const selection = config.selection
  if (!selection) {
    errors.push('缺少匹配策略')
  } else if (selection.policy === 'unique') {
    if (selection.index != null) errors.push('「必须唯一」不应携带序号')
  } else if (selection.policy === 'index') {
    if (selection.index == null || !Number.isInteger(selection.index) || selection.index < 1) errors.push('「指定第 N 个」须为正整数序号')
  } else {
    errors.push('匹配策略须为唯一或指定序号')
  }

  return errors
}

function validateConditions(conds: SmartCondition[], label: string, errors: string[]): void {
  if (conds.length < 1 || conds.length > SMART_LIMITS.conditionsMax) {
    errors.push(`${label}的条件须为 1..${SMART_LIMITS.conditionsMax} 条，当前 ${conds.length}`)
    return
  }
  conds.forEach((c, ci) => {
    const cLabel = `${label} 第 ${ci + 1} 条条件`
    if (!ATTRIBUTE_VALUES.includes(c.attribute)) {
      errors.push(`${cLabel}属性无效`)
      return
    }
    const isBool = isBooleanAttribute(c.attribute)
    if (isBool) {
      if (c.operator !== 'equals') errors.push(`${cLabel}：布尔属性只能使用「等于」`)
      if (typeof c.value !== 'boolean') errors.push(`${cLabel}：布尔属性取值须为 true/false`)
    } else {
      if (!OPERATOR_VALUES.includes(c.operator)) {
        errors.push(`${cLabel}运算符无效`)
        return
      }
      if (typeof c.value !== 'string') {
        errors.push(`${cLabel}：取值须为文本`)
      } else {
        if (c.value.length < 1) errors.push(`${cLabel}：取值不能为空`)
        else if (c.value.length > SMART_LIMITS.valueMaxLen) errors.push(`${cLabel}：取值长度须为 1..${SMART_LIMITS.valueMaxLen}`)
        if (c.operator === 'regex') {
          try {
            // eslint-disable-next-line no-new -- 纯校验，仅测试可编译性
            new RegExp(c.value)
          } catch {
            errors.push(`${cLabel}：正则无效（按 Python re 语法校验）`)
          }
        }
      }
    }
  })
}

function validatePath(path: SmartPathSegment[], label: string, errors: string[]): void {
  if (path.length < 1 || path.length > SMART_LIMITS.pathMax) {
    errors.push(`${label}段数须为 1..${SMART_LIMITS.pathMax}，当前 ${path.length}`)
    return
  }
  path.forEach((seg, pi) => {
    const pLabel = `${label} 第 ${pi + 1} 段`
    if (!AXIS_VALUES.includes(seg.axis)) {
      errors.push(`${pLabel}轴线无效`)
      return
    }
    const requires = axisRequiresDepth(seg.axis)
    if (requires) {
      if (seg.depth == null || !Number.isInteger(seg.depth) || seg.depth < 1 || seg.depth > SMART_LIMITS.depthMax) {
        errors.push(`${pLabel}（${axisLabel(seg.axis)}）须填写 1..${SMART_LIMITS.depthMax} 层级`)
      }
    } else if (seg.depth != null) {
      errors.push(`${pLabel}（${axisLabel(seg.axis)}）不支持层级数值`)
    }
  })
}

// ---------------------------------------------------------------------------
// 摘要
// ---------------------------------------------------------------------------

function conditionText(c: SmartCondition): string {
  const label = attributeLabel(String(c.attribute))
  if (isBooleanAttribute(String(c.attribute))) return `${label}=${String(c.value)}`
  return `${label}${operatorLabel(String(c.operator))}${String(c.value ?? '')}`
}

export function smartLocatorSummary(config: SmartLocatorConfig | null | undefined): string {
  if (!config || !Array.isArray(config.alternatives) || !config.alternatives.length) return '智能定位'
  const alt = config.alternatives[0]
  const target = alt?.target
  if (!target || !target.length || !target[0]) return '智能定位'
  // 无有效取值（非布尔条件值为空串）视为空配置，回退默认文案
  const hasContent = target.some((c) => {
    if (isBooleanAttribute(String(c.attribute))) return true
    return String(c.value ?? '') !== ''
  })
  if (!hasContent) return '智能定位'

  const parts = target.slice(0, 2).map((c) => conditionText(c))
  let desc = parts.length ? parts.join(' + ') : '智能定位'
  if (target.length > 2) desc += ` + ${target.length - 2} 个条件`

  if (alt.anchor && alt.anchor.length) desc = `锚定 ${desc}`
  if (alt.path && alt.path.length) desc += ' 相对定位'

  const search = config.search
  if (search) {
    if (search.scroll) desc += ` ${search.direction === 'up' ? '向上' : '向下'}滑动${search.max_swipes}次`
    else desc += ' 不滚动'
  }
  const selection = config.selection
  if (selection && selection.policy === 'index' && selection.index != null) desc += ` 第${selection.index}个`
  return desc
}

// ---------------------------------------------------------------------------
// 状态帮助器（不可变更新，供组件薄绑定 & 单测）
// ---------------------------------------------------------------------------

/**
 * 更新第 altIdx 个候选的可变部分（anchor/path/target）。
 * 传 `scope='anchor'|'target'` + 数组更新函数时，返回新 config。
 */
function mapAlt(config: SmartLocatorConfig, altIdx: number, updater: (alt: SmartAlternative) => SmartAlternative): SmartLocatorConfig {
  return { ...config, alternatives: config.alternatives.map((alt, i) => (i === altIdx ? updater(alt) : alt)) }
}

export function addCondition(config: SmartLocatorConfig, altIdx: number, scope: SmartConditionScope): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt[scope] ?? []
    if (list.length >= SMART_LIMITS.conditionsMax) return alt
    return { ...alt, [scope]: [...list, defaultCondition()] }
  })
}

export function removeCondition(config: SmartLocatorConfig, altIdx: number, scope: SmartConditionScope, condIdx: number): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt[scope] ?? []
    if (condIdx < 0 || condIdx >= list.length) return alt
    return { ...alt, [scope]: list.filter((_, i) => i !== condIdx) }
  })
}

export function moveCondition(config: SmartLocatorConfig, altIdx: number, scope: SmartConditionScope, condIdx: number, dir: 1 | -1): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt[scope] ?? []
    const to = condIdx + dir
    if (condIdx < 0 || condIdx >= list.length || to < 0 || to >= list.length) return alt
    const next = [...list]
    const [item] = next.splice(condIdx, 1)
    next.splice(to, 0, item)
    return { ...alt, [scope]: next }
  })
}

/** 把条件切到布尔属性：operator 固定 equals，值翻转为布尔。 */
export function toggleBoolValue(config: SmartLocatorConfig, altIdx: number, scope: SmartConditionScope, condIdx: number): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt[scope] ?? []
    if (condIdx < 0 || condIdx >= list.length) return alt
    const cur = list[condIdx]
    const nextVal = typeof cur.value === 'boolean' ? !cur.value : true
    return { ...alt, [scope]: list.map((c, i) => (i === condIdx ? { ...c, operator: 'equals' as SmartOperator, value: nextVal } : c)) }
  })
}

export function addAlternative(config: SmartLocatorConfig): SmartLocatorConfig {
  if (config.alternatives.length >= SMART_LIMITS.alternativesMax) return config
  return {
    ...config,
    alternatives: [...config.alternatives, { anchor: [], path: [], target: [defaultCondition()] }],
  }
}

export function removeAlternative(config: SmartLocatorConfig, altIdx: number): SmartLocatorConfig {
  if (config.alternatives.length <= 1 || altIdx < 0 || altIdx >= config.alternatives.length) return config
  return { ...config, alternatives: config.alternatives.filter((_, i) => i !== altIdx) }
}

export function moveAlternative(config: SmartLocatorConfig, altIdx: number, dir: 1 | -1): SmartLocatorConfig {
  const to = altIdx + dir
  if (altIdx < 0 || altIdx >= config.alternatives.length || to < 0 || to >= config.alternatives.length) return config
  const next = [...config.alternatives]
  const [item] = next.splice(altIdx, 1)
  next.splice(to, 0, item)
  return { ...config, alternatives: next }
}

export function addPathSegment(config: SmartLocatorConfig, altIdx: number): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt.path ?? []
    if (list.length >= SMART_LIMITS.pathMax) return alt
    return { ...alt, path: [...list, defaultPathSegment()] }
  })
}

export function removePathSegment(config: SmartLocatorConfig, altIdx: number, segIdx: number): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt.path ?? []
    if (segIdx < 0 || segIdx >= list.length) return alt
    return { ...alt, path: list.filter((_, i) => i !== segIdx) }
  })
}

export function movePathSegment(config: SmartLocatorConfig, altIdx: number, segIdx: number, dir: 1 | -1): SmartLocatorConfig {
  return mapAlt(config, altIdx, (alt) => {
    const list = alt.path ?? []
    const to = segIdx + dir
    if (segIdx < 0 || segIdx >= list.length || to < 0 || to >= list.length) return alt
    const next = [...list]
    const [item] = next.splice(segIdx, 1)
    next.splice(to, 0, item)
    return { ...alt, path: next }
  })
}
