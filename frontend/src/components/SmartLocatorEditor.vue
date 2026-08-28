<script setup lang="ts">
// 智能元素定位配置编辑器（共享组件）——受控 v-model；所有结构变更走 utils 纯函数，组件仅做薄绑定。
import { computed, onMounted, ref, watch } from 'vue'

import {
  SMART_ATTRIBUTES,
  SMART_AXES,
  SMART_LIMITS,
  SMART_OPERATORS,
  addAlternative,
  addCondition,
  addPathSegment,
  axisLabel,
  axisRequiresDepth,
  cloneSmartConfig,
  createDefaultConfig,
  isBooleanAttribute,
  moveAlternative,
  moveCondition,
  movePathSegment,
  removeAlternative,
  removeCondition,
  removePathSegment,
  smartLocatorSummary,
  validateSmartConfig,
  type SmartCondition,
  type SmartConditionScope,
  type SmartLocatorConfig,
  type SmartPathSegment,
} from '@/utils/smartLocator'

const props = defineProps<{ modelValue: SmartLocatorConfig | null }>()
const emit = defineEmits<{ 'update:modelValue': [config: SmartLocatorConfig | null] }>()

// 本地草稿：与外部 modelValue 解耦，编辑不破坏 props；深拷贝避免共享引用
const draft = ref<SmartLocatorConfig>(props.modelValue ? (cloneSmartConfig(props.modelValue) as SmartLocatorConfig) : createDefaultConfig())
const expandedAlts = ref<number[]>(draft.value.alternatives.map((_, i) => i))

let self = false
function emitChange() {
  self = true
  emit('update:modelValue', cloneSmartConfig(draft.value))
}

// 任一草稿改动 → 提交整体 config（结构变更与字段编辑统一走这里）
watch(draft, () => emitChange(), { deep: true })
// 外部重设（打开/编辑另一行）时回填；忽略自身 emit 触发的同步
watch(
  () => props.modelValue,
  (val) => {
    if (self) {
      self = false
      return
    }
    draft.value = val ? (cloneSmartConfig(val) as SmartLocatorConfig) : createDefaultConfig()
  },
)

// 初次进入若外部还没给配置，先播一份骨架给宿主，便于宿主立即拿到可编辑的 config
onMounted(() => {
  if (!props.modelValue) emitChange()
})

const validateErrors = computed(() => validateSmartConfig(draft.value))
const useIndex = computed({
  get: () => draft.value.selection.policy === 'index',
  set: (val: boolean) => {
    const sel = draft.value.selection
    draft.value.selection = val ? { policy: 'index', index: sel.index ?? 1 } : { policy: 'unique' }
  },
})

function isBool(attr: string): boolean {
  return isBooleanAttribute(attr)
}

function toggleAlt(ai: number) {
  expandedAlts.value = expandedAlts.value.includes(ai)
    ? expandedAlts.value.filter((i) => i !== ai)
    : [...expandedAlts.value, ai]
}

function altOpen(ai: number): boolean {
  return expandedAlts.value.includes(ai)
}

function condition(ai: number, scope: SmartConditionScope, ci: number): SmartCondition {
  return draft.value.alternatives[ai][scope]![ci]
}

function onConditionAttribute(ai: number, scope: SmartConditionScope, ci: number, attr: string) {
  const c = condition(ai, scope, ci)
  c.attribute = attr as SmartCondition['attribute']
  if (isBool(attr)) {
    c.operator = 'equals'
    if (typeof c.value !== 'boolean') c.value = true
  } else if (typeof c.value === 'boolean') {
    c.value = ''
  }
}

function setConditionValue(ai: number, scope: SmartConditionScope, ci: number, v: string) {
  condition(ai, scope, ci).value = v
}

function setConditionBool(ai: number, scope: SmartConditionScope, ci: number, v: string | number | boolean) {
  condition(ai, scope, ci).value = Boolean(v)
}

// ---------- 结构操作（纯函数，返回新 config 后回填草稿） ----------

function onAddAlt() {
  const before = draft.value.alternatives.length
  draft.value = addAlternative(draft.value)
  if (draft.value.alternatives.length > before) expandedAlts.value = [...expandedAlts.value, before]
}

function onRemoveAlt(ai: number) {
  draft.value = removeAlternative(draft.value, ai)
  // 后续候选索引前移一位，展开状态同步下移
  expandedAlts.value = expandedAlts.value
    .filter((i) => i !== ai)
    .map((i) => (i > ai ? i - 1 : i))
}

function onMoveAlt(ai: number, dir: 1 | -1) {
  draft.value = moveAlternative(draft.value, ai, dir)
}

function onAddCondition(ai: number, scope: SmartConditionScope) {
  draft.value = addCondition(draft.value, ai, scope)
}

function onRemoveCondition(ai: number, scope: SmartConditionScope, ci: number) {
  draft.value = removeCondition(draft.value, ai, scope, ci)
}

function onMoveCondition(ai: number, scope: SmartConditionScope, ci: number, dir: 1 | -1) {
  draft.value = moveCondition(draft.value, ai, scope, ci, dir)
}

function onAddPath(ai: number) {
  draft.value = addPathSegment(draft.value, ai)
}

function onPathAxisChange(ai: number, si: number, axis: string) {
  const seg = draft.value.alternatives[ai].path![si]
  seg.axis = axis as SmartPathSegment['axis']
  if (axisRequiresDepth(axis)) {
    if (seg.depth == null) seg.depth = 1
  } else {
    seg.depth = undefined
  }
}

function onRemovePath(ai: number, si: number) {
  draft.value = removePathSegment(draft.value, ai, si)
}

function onMovePath(ai: number, si: number, dir: 1 | -1) {
  draft.value = movePathSegment(draft.value, ai, si, dir)
}

function summary(): string {
  return smartLocatorSummary(draft.value)
}
</script>

<template>
  <div class="smart-editor">
    <div class="section">
      <div class="section-head">
        <span class="section-title">候选规则</span>
        <span class="v2-aux">至少 1 条，至多 {{ SMART_LIMITS.alternativesMax }} 条</span>
      </div>
      <div class="alt-list">
        <div v-for="(alt, ai) in draft.alternatives" :key="ai" class="alt-card">
          <div class="alt-head" @click="toggleAlt(ai)">
            <span class="caret">{{ altOpen(ai) ? '▾' : '▸' }}</span>
            <span class="alt-title">{{ ai === 0 ? '首选规则' : `备用规则 ${ai + 1}` }}</span>
            <span class="v2-aux alt-summary" :title="summary()">{{ smartLocatorSummary(alt ? { ...draft, alternatives: [alt] } : draft) }}</span>
            <span class="alt-actions" @click.stop>
              <el-button size="small" text :disabled="ai === 0" @click="onMoveAlt(ai, -1)">上移</el-button>
              <el-button size="small" text :disabled="ai === draft.alternatives.length - 1" @click="onMoveAlt(ai, 1)">下移</el-button>
              <el-button size="small" type="danger" text :disabled="draft.alternatives.length <= 1" @click="onRemoveAlt(ai)">删除</el-button>
            </span>
          </div>

          <div v-if="altOpen(ai)" class="alt-body">
            <!-- 锚点条件 -->
            <div class="sub-section">
              <div class="sub-title">锚点条件（可选）</div>
              <div v-for="(cond, ci) in (alt.anchor ?? [])" :key="ci" class="cond-row">
                <el-select :model-value="cond.attribute" class="w-attr" @update:model-value="onConditionAttribute(ai, 'anchor', ci, String($event))">
                  <el-option v-for="a in SMART_ATTRIBUTES" :key="a.value" :label="a.label" :value="a.value" />
                </el-select>
                <el-select v-model="cond.operator" class="w-op" :disabled="isBool(String(cond.attribute))">
                  <el-option v-for="o in SMART_OPERATORS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-switch v-if="isBool(String(cond.attribute))" :model-value="Boolean(cond.value)" class="w-switch" @change="setConditionBool(ai, 'anchor', ci, $event)" />
                <el-input v-else :model-value="String(cond.value ?? '')" class="w-val" placeholder="匹配取值" @update:model-value="setConditionValue(ai, 'anchor', ci, String($event))" />
                <span class="cond-ops">
                  <el-button size="small" text :disabled="ci === 0" @click="onMoveCondition(ai, 'anchor', ci, -1)">↑</el-button>
                  <el-button size="small" text :disabled="ci === (alt.anchor?.length ?? 0) - 1" @click="onMoveCondition(ai, 'anchor', ci, 1)">↓</el-button>
                  <el-button size="small" text type="danger" @click="onRemoveCondition(ai, 'anchor', ci)">删</el-button>
                </span>
              </div>
              <el-button v-if="(alt.anchor?.length ?? 0) < SMART_LIMITS.conditionsMax" size="small" text type="primary" @click="onAddCondition(ai, 'anchor')">
                + 添加锚点条件
              </el-button>
              <div v-else class="v2-aux">锚点条件已达上限</div>
            </div>

            <!-- 相对路径 -->
            <div class="sub-section">
              <div class="sub-title">相对路径（可选）</div>
              <div v-for="(seg, si) in (alt.path ?? [])" :key="si" class="cond-row">
                <el-select :model-value="seg.axis" class="w-attr" @update:model-value="onPathAxisChange(ai, si, String($event))">
                  <el-option v-for="a in SMART_AXES" :key="a.value" :label="a.label" :value="a.value" />
                </el-select>
                <span v-if="axisRequiresDepth(seg.axis)" class="depth-label">层级</span>
                <el-input-number v-if="axisRequiresDepth(seg.axis)" v-model="seg.depth" :min="1" :max="SMART_LIMITS.depthMax" size="small" class="w-depth" />
                <span v-else class="v2-aux depth-hint">（{{ axisLabel(seg.axis) }}不需要层级）</span>
                <span class="cond-ops">
                  <el-button size="small" text :disabled="si === 0" @click="onMovePath(ai, si, -1)">↑</el-button>
                  <el-button size="small" text :disabled="si === (alt.path?.length ?? 0) - 1" @click="onMovePath(ai, si, 1)">↓</el-button>
                  <el-button size="small" text type="danger" @click="onRemovePath(ai, si)">删</el-button>
                </span>
              </div>
              <el-button v-if="(alt.path?.length ?? 0) < SMART_LIMITS.pathMax" size="small" text type="primary" @click="onAddPath(ai)">
                + 添加路径段
              </el-button>
              <div v-else class="v2-aux">相对路径已达 {{ SMART_LIMITS.pathMax }} 段上限</div>
            </div>

            <!-- 匹配条件 -->
            <div class="sub-section">
              <div class="sub-title">匹配条件（必填）</div>
              <div v-for="(cond, ci) in alt.target" :key="ci" class="cond-row">
                <el-select :model-value="cond.attribute" class="w-attr" @update:model-value="onConditionAttribute(ai, 'target', ci, String($event))">
                  <el-option v-for="a in SMART_ATTRIBUTES" :key="a.value" :label="a.label" :value="a.value" />
                </el-select>
                <el-select v-model="cond.operator" class="w-op" :disabled="isBool(String(cond.attribute))">
                  <el-option v-for="o in SMART_OPERATORS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-switch v-if="isBool(String(cond.attribute))" :model-value="Boolean(cond.value)" class="w-switch" @change="setConditionBool(ai, 'target', ci, $event)" />
                <el-input v-else :model-value="String(cond.value ?? '')" class="w-val" placeholder="匹配取值" @update:model-value="setConditionValue(ai, 'target', ci, String($event))" />
                <span class="cond-ops">
                  <el-button size="small" text :disabled="ci === 0" @click="onMoveCondition(ai, 'target', ci, -1)">↑</el-button>
                  <el-button size="small" text :disabled="ci === alt.target.length - 1" @click="onMoveCondition(ai, 'target', ci, 1)">↓</el-button>
                  <el-button size="small" text type="danger" @click="onRemoveCondition(ai, 'target', ci)">删</el-button>
                </span>
              </div>
              <el-button v-if="alt.target.length < SMART_LIMITS.conditionsMax" size="small" text type="primary" @click="onAddCondition(ai, 'target')">
                + 添加匹配条件
              </el-button>
            </div>

            <el-button v-if="draft.alternatives.length < SMART_LIMITS.alternativesMax" size="small" text type="primary" class="add-alt" @click="onAddAlt">+ 添加备用规则</el-button>
            <div v-else class="v2-aux">候选规则已达 {{ SMART_LIMITS.alternativesMax }} 条上限</div>
          </div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">滚动配置</div>
      <div class="field-row">
        <span class="f-label">启用滚动</span>
        <el-switch v-model="draft.search.scroll" />
        <span class="f-gap"></span>
        <span class="f-label">方向</span>
        <el-select v-model="draft.search.direction" size="small" class="w-dir" :disabled="!draft.search.scroll">
          <el-option label="向上" value="up" />
          <el-option label="向下" value="down" />
        </el-select>
      </div>
      <div class="field-row">
        <span class="f-label">最大滑动次数</span>
        <el-input-number v-model="draft.search.max_swipes" :min="1" :max="SMART_LIMITS.maxSwipesMax" size="small" class="w-num" />
        <span class="f-label">滑动时长(ms)</span>
        <el-input-number v-model="draft.search.duration_ms" :min="SMART_LIMITS.durationMinMs" :max="SMART_LIMITS.durationMaxMs" :step="50" size="small" class="w-num" />
        <span class="f-label">稳定等待(ms)</span>
        <el-input-number v-model="draft.search.settle_ms" :min="0" :max="SMART_LIMITS.settleMaxMs" :step="50" size="small" class="w-num" />
      </div>
    </div>

    <div class="section">
      <div class="section-title">匹配策略</div>
      <div class="field-row">
        <el-switch v-model="useIndex" />
        <span class="f-label">{{ useIndex ? '显式第 N 个' : '必须唯一（推荐）' }}</span>
        <el-input-number v-if="useIndex" v-model="draft.selection.index" :min="1" size="small" class="w-num" />
      </div>
      <el-alert
        v-if="useIndex"
        type="warning"
        :closable="false"
        show-icon
        class="index-alert"
        title="仅当页面元素顺序绝对稳定时使用；顺序变化会误定位或越界失败。"
      />
      <div v-else class="v2-aux">按唯一匹配定位元素；同屏存在多个匹配时判定为「不唯一」并报错，不会继续滚动或查找。</div>
    </div>

    <div v-if="validateErrors.length" class="errors">
      <div v-for="(e, i) in validateErrors" :key="i" class="error-line">{{ e }}</div>
    </div>
  </div>
</template>

<style scoped>
.smart-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.section {
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 10px 12px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.section-title {
  font-weight: 600;
  font-size: 13px;
}
.alt-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.alt-card {
  border: 1px solid var(--border);
  border-radius: 8px;
}
.alt-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  cursor: pointer;
  user-select: none;
}
.alt-head:hover {
  background: var(--el-fill-color-light);
}
.caret {
  color: var(--text-2);
  font-size: 12px;
  width: 12px;
}
.alt-title {
  font-weight: 600;
  font-size: 13px;
  flex-shrink: 0;
}
.alt-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.alt-actions {
  flex-shrink: 0;
}
.alt-body {
  border-top: 1px solid var(--border);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sub-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sub-title {
  font-size: 12px;
  color: var(--text-2);
  font-weight: 600;
}
.cond-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.w-attr {
  width: 120px;
}
.w-op {
  width: 96px;
}
.w-val {
  /* 保底可输入：任何容器宽度下至少 160px；flex-wrap 时掉到下一行仍占满剩余宽度 */
  flex: 1 1 160px;
  min-width: 160px;
}
.w-switch {
  flex-shrink: 0;
}
.w-depth {
  width: 80px;
}
.w-dir {
  width: 100px;
}
.w-num {
  width: 110px;
}
.depth-label {
  font-size: 12px;
  color: var(--text-2);
  white-space: nowrap;
}
.depth-hint {
  flex: 1 1 120px;
  min-width: 120px;
  font-size: 12px;
}
.cond-ops {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
}
.add-alt {
  align-self: flex-start;
}
.field-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
  flex-wrap: wrap;
}
.f-label {
  font-size: 12px;
  color: var(--text-2);
  white-space: nowrap;
}
.f-gap {
  flex: 1;
}
.index-alert {
  margin-top: 6px;
}
.errors {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.error-line {
  color: #f56c6c;
  font-size: 12px;
  padding: 4px;
}
</style>
