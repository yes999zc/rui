<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from './api'

const version = ref('')
const online = ref(false)

onMounted(async () => {
  try {
    const h = await api.health()
    version.value = h.version
    online.value = true
  } catch {
    online.value = false
  }
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        house-ear<small>v{{ version || '—' }} · {{ online ? 'control plane online' : 'offline' }}</small>
      </div>
      <nav class="nav">
        <RouterLink to="/overview">概览</RouterLink>
        <RouterLink to="/devices">设备</RouterLink>
        <RouterLink to="/assistant">助手</RouterLink>
        <RouterLink to="/voice">语音</RouterLink>
        <RouterLink to="/models">模型</RouterLink>
        <RouterLink to="/tools">工具</RouterLink>
        <RouterLink to="/secrets">密钥</RouterLink>
        <RouterLink to="/health">体检</RouterLink>
        <RouterLink to="/logs">日志</RouterLink>
      </nav>
    </aside>
    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
