import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

// V2 §6.1：布局状态——侧栏折叠、最近访问项目、表格密度（持久化到 localStorage）。
const LS_KEY = 'v2_layout'

interface LayoutState {
  collapsed: boolean
  density: 'default' | 'compact'
  recentProjects: number[]
}

function load(): LayoutState {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw) as LayoutState
  } catch {
    /* 忽略损坏缓存 */
  }
  return { collapsed: false, density: 'default', recentProjects: [] }
}

export const useLayoutStore = defineStore('layout', () => {
  const saved = load()
  const collapsed = ref(saved.collapsed)
  const density = ref<'default' | 'compact'>(saved.density)
  const recentProjects = ref<number[]>(saved.recentProjects)

  watch(
    [collapsed, density, recentProjects],
    ([c, d, r]) => {
      localStorage.setItem(LS_KEY, JSON.stringify({ collapsed: c, density: d, recentProjects: r }))
    },
    { deep: true },
  )

  function toggleCollapsed() {
    collapsed.value = !collapsed.value
  }

  function visitProject(projectId: number) {
    const list = recentProjects.value.filter((id) => id !== projectId)
    list.unshift(projectId)
    recentProjects.value = list.slice(0, 5)
  }

  return { collapsed, density, recentProjects, toggleCollapsed, visitProject }
})