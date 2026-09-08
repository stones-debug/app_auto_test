import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const drawerSource = readFileSync(resolve(process.cwd(), 'src/components/ProfileOverrideDrawer.vue'), 'utf8')

describe('覆盖配置抽屉加载与布局契约', () => {
  it('首次只请求元素分页，变量在切换到变量页时懒加载', () => {
    expect(drawerSource).toContain('listProfileOverrides(profileId, { include_nodes: false })')
    expect(drawerSource).toContain('page_size: elementPageSize.value')
    expect(drawerSource).toContain('function onTabChange')
    expect(drawerSource).toContain("name === 'variable' && variablesLoadedForProfile !== props.profileId")
    expect(drawerSource).toContain('@tab-change="onTabChange"')
  })

  it('元素分页包含搜索、页码和页大小变更，避免截断超过 200 条的数据', () => {
    expect(drawerSource).toContain('v-model:current-page="elementPage"')
    expect(drawerSource).toContain('v-model:page-size="elementPageSize"')
    expect(drawerSource).toContain('@current-change="onElementPageChange"')
    expect(drawerSource).toContain('@size-change="onElementPageSizeChange"')
    expect(drawerSource).toContain('placeholder="搜索元素名称"')
    expect(drawerSource).not.toContain('page_size: 200')
  })

  it('布局使用抽屉剩余空间，错误状态提供重试入口', () => {
    expect(drawerSource).toContain('height="100%"')
    expect(drawerSource).toContain('flex: 1;')
    expect(drawerSource).not.toContain('max-height="520"')
    expect(drawerSource).toContain('elementError')
    expect(drawerSource).toContain('variableError')
    expect(drawerSource).toContain('元素覆盖加载失败，请重试')
    expect(drawerSource).toContain('变量覆盖加载失败，请重试')
  })

  it('异步响应按档案和请求序列校验，旧档案结果不会覆盖当前数据', () => {
    expect(drawerSource).toContain('props.profileId === profileId')
    expect(drawerSource).toContain('generation === profileGeneration')
    expect(drawerSource).toContain('elementRequestSequence : variableRequestSequence')
    expect(drawerSource).toContain('if (!isCurrent(profileId, requestId, \'element\')) return')
    expect(drawerSource).toContain('if (!isCurrent(profileId, requestId, \'variable\')) return')
  })
})
