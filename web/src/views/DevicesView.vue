<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Device } from '../api'

const devices = ref<Device[]>([])
const error = ref('')

onMounted(async () => {
  try {
    devices.value = await api.devices()
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <h1>设备</h1>
  <p class="sub">每台音箱一个 adapter，能力位决定它能做什么。缺 reference 的设备无法在放歌时插话。</p>

  <p v-if="error" class="fail">{{ error }}</p>
  <p v-if="!devices.length && !error" class="empty">
    还没有设备。编辑 <code>config.yaml</code> 的 <code>devices</code> 后重启。
  </p>

  <div v-for="d in devices" :key="d.id" class="card">
    <h2>{{ d.room || d.id }} <span class="tag">{{ d.adapter }}</span></h2>
    <div class="row"><span class="k">id</span><span class="v">{{ d.id }}</span></div>
    <div class="row"><span class="k">host</span><span class="v">{{ d.host }}</span></div>
    <div class="row"><span class="k">model</span><span class="v">{{ d.model }}</span></div>
    <div class="row">
      <span class="k">capabilities</span>
      <span class="v"><span v-for="c in d.capabilities" :key="c" class="tag">{{ c }}</span></span>
    </div>
    <div class="row">
      <span class="k">barge-in</span>
      <span class="v" :class="d.missing_for_barge_in.length ? 'warn' : 'ok'">
        {{ d.missing_for_barge_in.length ? '不可用，缺 ' + d.missing_for_barge_in.join(', ') : '可用' }}
      </span>
    </div>
  </div>
</template>
