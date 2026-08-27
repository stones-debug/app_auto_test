import { describe, expect, it } from 'vitest'

import {
  SMART_OPERATORS,
  addAlternative,
  addCondition,
  addPathSegment,
  buildLocatorPayload,
  cloneSmartConfig,
  createDefaultConfig,
  defaultCondition,
  moveAlternative,
  moveCondition,
  movePathSegment,
  removeAlternative,
  removeCondition,
  removePathSegment,
  smartLocatorSummary,
  toggleBoolValue,
  validateSmartConfig,
  type SmartCondition,
  type SmartLocatorConfig,
} from '@/utils/smartLocator'
import { LOCATOR_TYPES } from '@/api/elements'

function setTarget(overrides: Partial<SmartCondition>): SmartLocatorConfig {
  const cfg = createDefaultConfig()
  cfg.alternatives[0].target[0] = { ...cfg.alternatives[0].target[0], ...overrides } as SmartCondition
  return cfg
}

describe('createDefaultConfig / 数据形态', () => {
  it('默认结构：version 1、单个候选、单个目标条件、默认滚动与策略', () => {
    const cfg = createDefaultConfig()
    expect(cfg.version).toBe(1)
    expect(cfg.alternatives).toHaveLength(1)
    expect(cfg.alternatives[0].target).toHaveLength(1)
    expect(cfg.alternatives[0].target[0]).toEqual(defaultCondition())
    expect(cfg.search).toEqual({ scroll: true, direction: 'up', max_swipes: 8, duration_ms: 500, settle_ms: 300 })
    expect(cfg.selection).toEqual({ policy: 'unique' })
  })

  it('普通/智能提交 payload 形态不同：smart value=null 带 config，普通 value 不空 且 config=null', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].target[0].value = '登录'
    const smart = buildLocatorPayload('smart', '', cfg)
    expect(smart.locator_type).toBe('smart')
    expect(smart.locator_value).toBeNull()
    expect(smart.locator_config).toEqual({
      version: 1,
      alternatives: [{ target: [{ attribute: 'text', operator: 'equals', value: '登录' }] }],
      search: { scroll: true, direction: 'up', max_swipes: 8, duration_ms: 500, settle_ms: 300 },
      selection: { policy: 'unique' },
    })

    const normal = buildLocatorPayload('id', 'com.demo:id/btn', null)
    expect(normal).toEqual({ locator_type: 'id', locator_value: 'com.demo:id/btn', locator_config: null })
  })

  it('toSerializableConfig 剔除空的 anchor/path 数组，避免空数组触发 1..N 校验', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].anchor = []
    cfg.alternatives[0].path = []
    const serialized = buildLocatorPayload('smart', '', cfg).locator_config as SmartLocatorConfig
    const alt = serialized.alternatives[0]
    expect(alt.anchor).toBeUndefined()
    expect(alt.path).toBeUndefined()
  })
})

describe('条件编辑', () => {
  it('addCondition 追加到 target/anchor，索引稳定', () => {
    let cfg = createDefaultConfig()
    cfg = addCondition(cfg, 0, 'target')
    expect(cfg.alternatives[0].target).toHaveLength(2)
    expect(cfg.alternatives[0].target[1]).toEqual(defaultCondition())
  })

  it('removeCondition 按索引删除，不影响其他条件', () => {
    let cfg = createDefaultConfig()
    cfg = addCondition(cfg, 0, 'target')
    cfg.alternatives[0].target[1].value = '第二个'
    cfg = removeCondition(cfg, 0, 'target', 0)
    expect(cfg.alternatives[0].target).toHaveLength(1)
    expect(cfg.alternatives[0].target[0].value).toBe('第二个')
  })

  it('moveCondition 上/下移动保持顺序，越界不改变', () => {
    let cfg = createDefaultConfig()
    cfg = addCondition(cfg, 0, 'target')
    cfg = addCondition(cfg, 0, 'target')
    cfg.alternatives[0].target[0].value = 'a'
    cfg.alternatives[0].target[1].value = 'b'
    cfg.alternatives[0].target[2].value = 'c'
    cfg = moveCondition(cfg, 0, 'target', 1, -1)
    expect(cfg.alternatives[0].target.map((c) => c.value)).toEqual(['b', 'a', 'c'])
    cfg = moveCondition(cfg, 0, 'target', 0, -1)
    expect(cfg.alternatives[0].target.map((c) => c.value)).toEqual(['b', 'a', 'c'])
  })

  it('toggleBoolValue 翻转为布尔值并固定 equals', () => {
    let cfg = setTarget({ value: 'x' })
    cfg = toggleBoolValue(cfg, 0, 'target', 0)
    const c = cfg.alternatives[0].target[0]
    expect(c.value).toBe(true)
    expect(c.operator).toBe('equals')
    // 再次翻转
    cfg = toggleBoolValue(cfg, 0, 'target', 0)
    expect(cfg.alternatives[0].target[0].value).toBe(false)
  })
})

describe('备用规则', () => {
  it('addAlternative/removeAlternative/moveAlternative 行为正确', () => {
    let cfg = createDefaultConfig()
    cfg = addAlternative(cfg)
    expect(cfg.alternatives).toHaveLength(2)
    cfg = moveAlternative(cfg, 1, -1)
    expect(cfg.alternatives[1].target[0].value).toBe('') // 第一候选仍为默认
    cfg = removeAlternative(cfg, 1)
    expect(cfg.alternatives).toHaveLength(1)
  })

  it('规则数上限 10：新增到第 10 个后不再增加', () => {
    let cfg = createDefaultConfig()
    for (let i = 0; i < 20; i++) cfg = addAlternative(cfg)
    expect(cfg.alternatives.length).toBe(10)
    // 默认骨架匹配值为空，填上一个合法值再校验整体
    cfg.alternatives.forEach((a) => { a.target[0].value = 'x' })
    expect(validateSmartConfig(cfg)).toHaveLength(0)
  })

  it('removeAlternative 至少保留 1 条', () => {
    const cfg = createDefaultConfig()
    expect(removeAlternative(cfg, 0).alternatives).toHaveLength(1)
  })
})

describe('相对路径', () => {
  it('addPathSegment 追加段，默认 child 无 depth；上限 3 段后不再增加', () => {
    let cfg = createDefaultConfig()
    cfg.alternatives[0].path = []
    cfg = addPathSegment(cfg, 0)
    expect(cfg.alternatives[0].path).toHaveLength(1)
    for (let i = 0; i < 10; i++) cfg = addPathSegment(cfg, 0)
    expect(cfg.alternatives[0].path!.length).toBe(3)
  })

  it('ancestor/parent 无 depth → 校验错误', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].path = [{ axis: 'ancestor' }]
    expect(validateSmartConfig(cfg).some((e) => e.includes('须填写'))).toBe(true)
  })

  it('非 ancestor/parent 带 depth → 校验错误（child/descendant 等不支持层级）', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].path = [{ axis: 'child', depth: 2 }]
    expect(validateSmartConfig(cfg).some((e) => e.includes('不支持层级'))).toBe(true)
  })

  it('ancestor depth 5 合法，6 越界报错', () => {
    const ok = createDefaultConfig()
    ok.alternatives[0].target[0].value = 'x'
    ok.alternatives[0].path = [{ axis: 'ancestor', depth: 5 }]
    expect(validateSmartConfig(ok)).toHaveLength(0)

    const bad = createDefaultConfig()
    bad.alternatives[0].target[0].value = 'x'
    bad.alternatives[0].path = [{ axis: 'ancestor', depth: 6 }]
    expect(validateSmartConfig(bad).some((e) => e.includes('须填写'))).toBe(true)
  })

  it('movePathSegment / removePathSegment 索引稳定', () => {
    let cfg = createDefaultConfig()
    cfg.alternatives[0].path = [{ axis: 'child' }, { axis: 'ancestor', depth: 2 }, { axis: 'following_sibling' }]
    cfg = movePathSegment(cfg, 0, 1, -1)
    expect(cfg.alternatives[0].path!.map((p) => p.axis)).toEqual(['ancestor', 'child', 'following_sibling'])
    cfg = removePathSegment(cfg, 0, 0)
    expect(cfg.alternatives[0].path!.map((p) => p.axis)).toEqual(['child', 'following_sibling'])
  })
})

describe('滚动配置', () => {
  it('max_swipes 0 / 21 拦截，默认 8 合法', () => {
    const zero = createDefaultConfig(); zero.search.max_swipes = 0
    expect(validateSmartConfig(zero).some((e) => e.includes('最大滑动次数'))).toBe(true)
    const bail = createDefaultConfig(); bail.search.max_swipes = 21
    expect(validateSmartConfig(bail).some((e) => e.includes('最大滑动次数'))).toBe(true)
    const ok = createDefaultConfig()
    ok.alternatives[0].target[0].value = 'x'
    expect(validateSmartConfig(ok)).toHaveLength(0)
  })

  it('duration 越界拦截（50 / 3000）', () => {
    const lo = createDefaultConfig(); lo.search.duration_ms = 50
    expect(validateSmartConfig(lo).some((e) => e.includes('滑动时长'))).toBe(true)
    const hi = createDefaultConfig(); hi.search.duration_ms = 3000
    expect(validateSmartConfig(hi).some((e) => e.includes('滑动时长'))).toBe(true)
  })
})

describe('非法配置阻止提交', () => {
  it('未知 attribute 报错', () => {
    const cfg = setTarget({ attribute: 'content_desc' as never } as Partial<SmartCondition>)
    cfg.alternatives[0].target[0] = { attribute: 'weird' as SmartCondition['attribute'], operator: 'equals', value: 'x' }
    expect(validateSmartConfig(cfg).some((e) => e.includes('属性无效'))).toBe(true)
  })

  it('布尔属性不能用 contains（仅 equals）', () => {
    const cfg = setTarget({ attribute: 'clickable', operator: 'contains', value: true })
    expect(validateSmartConfig(cfg).some((e) => e.includes('布尔属性'))).toBe(true)
  })

  it('正则 value「[」不可编译报错', () => {
    const cfg = setTarget({ operator: 'regex', value: '[' })
    expect(validateSmartConfig(cfg).some((e) => e.includes('正则无效'))).toBe(true)
  })

  it('非布尔 value 为空 / 超 200 字符报错', () => {
    const empty = setTarget({ value: '' })
    expect(validateSmartConfig(empty).some((e) => e.includes('不能为空'))).toBe(true)
    const tooLong = setTarget({ value: 'x'.repeat(201) })
    expect(validateSmartConfig(tooLong).some((e) => e.includes('长度'))).toBe(true)
  })

  it('非法配置返回非空数组', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].target[0].value = ''
    expect(validateSmartConfig(cfg).length).toBeGreaterThan(0)
    expect(validateSmartConfig(null).length).toBeGreaterThan(0)
  })
})

describe('覆盖回显与保存（JSON 往返一致）', () => {
  it('config 序列化/反序列化后校验结果一致', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].target[0].value = '登录'
    cfg.alternatives[0].path = [{ axis: 'ancestor', depth: 2 }]
    cfg.selection = { policy: 'index', index: 2 }
    const back = JSON.parse(JSON.stringify(cfg)) as SmartLocatorConfig
    expect(validateSmartConfig(cfg)).toEqual(validateSmartConfig(back))
    expect(back).toEqual(cfg)
  })

  it('cloneSmartConfig 生成独立副本', () => {
    const cfg = createDefaultConfig()
    const copy = cloneSmartConfig(cfg)!
    copy.alternatives[0].target[0].value = '改'
    expect(cfg.alternatives[0].target[0].value).toBe('')
  })
})

describe('smartLocatorSummary 摘要', () => {
  it('目标摘要：文字等于 + 值', () => {
    const cfg = setTarget({ value: '登录' })
    expect(smartLocatorSummary(cfg)).toBe('文字等于登录 向上滑动8次')
  })

  it('滚动关闭显示「不滚动」', () => {
    const cfg = setTarget({ value: '登录' })
    cfg.search.scroll = false
    expect(smartLocatorSummary(cfg)).toContain('不滚动')
  })

  it('指定序号显示「第N个」', () => {
    const cfg = setTarget({ value: '登录' })
    cfg.selection = { policy: 'index', index: 3 }
    expect(smartLocatorSummary(cfg)).toContain('第3个')
  })

  it('锚定 / 相对定位 前缀与后缀', () => {
    const cfg = setTarget({ value: '登录' })
    cfg.alternatives[0].anchor = [{ attribute: 'class_name', operator: 'equals', value: 'TextView' }]
    cfg.alternatives[0].path = [{ axis: 'ancestor', depth: 1 }]
    const summary = smartLocatorSummary(cfg)
    expect(summary).toContain('锚定')
    expect(summary).toContain('相对定位')
  })

  it('超过 2 条条件显示「+ N 个条件」', () => {
    const cfg = createDefaultConfig()
    cfg.alternatives[0].target = [
      { attribute: 'text', operator: 'equals', value: '登录' },
      { attribute: 'class_name', operator: 'equals', value: 'TextView' },
      { attribute: 'package', operator: 'equals', value: 'com.demo' },
    ]
    expect(smartLocatorSummary(cfg)).toContain('+ 1 个条件')
  })

  it('空配置 / 空 candidates 返回「智能定位」', () => {
    expect(smartLocatorSummary(null)).toBe('智能定位')
    const empty = createDefaultConfig()
    empty.alternatives = []
    expect(smartLocatorSummary(empty)).toBe('智能定位')
  })
})

describe('LOCATOR_TYPES 提供 smart', () => {
  it('定位方式下拉包含智能定位（Android）', () => {
    expect(LOCATOR_TYPES).toContainEqual({ value: 'smart', label: '智能定位（Android）' })
  })

  it('运算符常量含 5 个运算符', () => {
    expect(SMART_OPERATORS.map((o) => o.value)).toEqual(['equals', 'contains', 'starts_with', 'ends_with', 'regex'])
  })
})
