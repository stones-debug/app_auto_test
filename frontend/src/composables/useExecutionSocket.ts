import { useWebSocket } from '@vueuse/core'
import { ref } from 'vue'

export function executionWsUrl(executionId: number, token: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/executions/${executionId}?token=${token}`
}

export function useExecutionSocket(executionId: number, token: string, onMessage: (msg: Record<string, unknown>) => void) {
  const connected = ref(false)
  const url = ref(executionWsUrl(executionId, token))
  const ws = useWebSocket(url, {
    autoReconnect: {
      retries: 10,
      delay: 1000,
      onFailed() {
        // 重连失败不提示，前端以 REST 拉取兜底
      },
    },
    heartbeat: {
      message: JSON.stringify({ type: 'ping' }),
      interval: 30000,
      pongTimeout: 5000,
    },
    onConnected: () => {
      connected.value = true
    },
    onDisconnected: () => {
      connected.value = false
    },
    onMessage: (_ws, event) => {
      try {
        onMessage(JSON.parse(event.data as string))
      } catch {
        // 忽略非法消息
      }
    },
  })

  function close() {
    ws.close()
  }

  return { connected, close }
}
