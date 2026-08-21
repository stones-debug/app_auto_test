import { defineStore } from 'pinia'
import { ref } from 'vue'

import { getDefaultDevice, type DefaultDevice } from '@/api/agents'

// V2 §6.1：设备状态——默认设备与可用性摘要，设置/解绑后失效。
export const useDeviceStore = defineStore('device', () => {
  const defaultDevice = ref<DefaultDevice>({
    device_id: null,
    device: null,
    available: false,
    reason: '',
  })
  const loadedAt = ref(0)
  let inFlight: Promise<void> | null = null

  async function loadDefault({ force = false } = {}) {
    if (!force && inFlight) return inFlight
    if (!force && loadedAt.value && Date.now() - loadedAt.value < 5000) return
    inFlight = (async () => {
      try {
        defaultDevice.value = await getDefaultDevice()
        loadedAt.value = Date.now()
      } finally {
        inFlight = null
      }
    })()
    return inFlight
  }

  function invalidate() {
    loadedAt.value = 0
    defaultDevice.value = { device_id: null, device: null, available: false, reason: '' }
  }

  return { defaultDevice, loadDefault, invalidate }
})