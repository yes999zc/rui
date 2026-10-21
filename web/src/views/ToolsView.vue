<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Config } from '../api'

const names = ref<string[]>([])
const cfg = ref<Config | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    const [t, c] = await Promise.all([api.tools(), api.config()])
    names.value = t.names
    cfg.value = c
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <h1>工具</h1>
  <p class="sub">本地工具、MCP 工具、Home Assistant 实体合并成一张注册表，按名字空间隔离。</p>

  <p v-if="error" class="fail">{{ error }}</p>

  <div class="card" v-if="cfg">
    <h2>暴露策略</h2>
    <div class="row"><span class="k">expose</span><span class="v">{{ cfg.tools.expose }}</span></div>
    <div class="row"><span class="k">whitelist</span><span class="v">{{ cfg.tools.whitelist.length }} 项</span></div>
    <div class="row"><span class="k">mcp servers</span><span class="v">{{ cfg.tools.mcp_servers.length }}</span></div>
    <p class="note">
      实体不一对一变工具：423 个实体会把提示词撑爆。默认走「域级模板 + 白名单实体」，
      上限 50 个工具，超出后按读取优先排序。
    </p>
  </div>

  <div class="card">
    <h2>已注册（{{ names.length }}）</h2>
    <p v-if="!names.length" class="empty">还没有工具。M1 接入 MCP 与 Home Assistant 扫描后在设备端自动填充。</p>
    <div class="row" v-for="n in names" :key="n"><span class="v"><code>{{ n }}</code></span></div>
  </div>
</template>
