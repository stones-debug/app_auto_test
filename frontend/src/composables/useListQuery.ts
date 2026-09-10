import { onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// V2 §4.1：列表查询——URL Query 同步、分页、防抖、取消旧请求。
// 返回 { page, pageSize, query, setFilter, load, loading, error }
export function useListQuery<T>(
  basePath: string,
  {
    defaultPageSize = 20,
    debounce = 300,
    fetch,
  }: {
    defaultPageSize?: number
    debounce?: number
    fetch: (params: Record<string, unknown>) => Promise<{ total: number; items: T[] }>
  },
) {
  const route = useRoute()
  const router = useRouter()
  const page = ref(Number(route.query.page ?? 1) || 1)
  const pageSize = ref(Number(route.query.page_size ?? defaultPageSize) || defaultPageSize)
  const query = ref<Record<string, unknown>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)
  const total = ref(0)
  const items = ref<T[]>([])
  let timer: ReturnType<typeof setTimeout> | null = null
  // 竞态保护：每次 load 领一个递增序号，响应回来时如果序号已过期就丢弃。
  // 单个「已卸载」布尔标志区分不了请求先后——快速翻页时旧页响应后到会覆盖新页数据。
  let requestSeq = 0

  function syncUrl() {
    const q: Record<string, string> = { page: String(page.value), page_size: String(pageSize.value) }
    for (const [k, v] of Object.entries(query.value)) {
      if (v !== '' && v !== null && v !== undefined) q[k] = String(v)
    }
    router.replace({ path: basePath, query: q })
  }

  async function load() {
    const seq = ++requestSeq
    loading.value = true
    error.value = null
    try {
      const params = { ...query.value, page: page.value, page_size: pageSize.value }
      const data = await fetch(params)
      if (seq !== requestSeq) return
      total.value = data.total
      items.value = data.items
    } catch (e) {
      if (seq !== requestSeq) return
      error.value = e instanceof Error ? e.message : '加载失败'
    } finally {
      // 只有最后一次请求才收尾，否则会把新请求的 loading 提前关掉
      if (seq === requestSeq) loading.value = false
    }
  }

  function setFilter(patch: Record<string, unknown>) {
    query.value = { ...query.value, ...patch }
    page.value = 1
    syncUrl()
    if (timer) clearTimeout(timer)
    timer = setTimeout(load, debounce)
  }

  function onPageChange(p: number) {
    page.value = p
    syncUrl()
    load()
  }

  function onSizeChange(s: number) {
    pageSize.value = s
    page.value = 1
    syncUrl()
    load()
  }

  function reset() {
    query.value = {}
    page.value = 1
    syncUrl()
    load()
  }

  onBeforeUnmount(() => {
    // 让在途响应失效
    requestSeq += 1
    if (timer) clearTimeout(timer)
  })

  return { page, pageSize, query, total, items, loading, error, load, setFilter, reset, onPageChange, onSizeChange }
}
