<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
if (!auth.user) {
  auth.fetchMe().catch(() => {})
}
</script>

<template>
  <router-view />
</template>