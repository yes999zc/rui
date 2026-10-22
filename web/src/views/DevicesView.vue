<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Device } from '../api'

const devices = ref<Device[]>([])
const error = ref('')
// The device's own mixer (mysoftvol 0-255). Read from the speaker, not from config: the
// vendor rewrites it on its own, so a cached number would be a lie within minutes.
const volume = ref<number | null>(null)
const volumeNote = ref('')

async function readVolume() {
  try {
    volume.value = (await api.deviceVolume()).volume
    volumeNote.value = ''
  } catch (e) {
    volumeNote.value = String(e)
  }
}

async function writeVolume(value: number) {
  try {
    volume.value = (await api.setDeviceVolume(value)).volume
    volumeNote.value = '已写入音箱'
  } catch (e) {
    volumeNote.value = String(e)
  }
}

onMounted(async () => {
  try {
    devices.value = await api.devices()
  } catch (e) {
    error.value = String(e)
  }
  await readVolume()
})
</script>

<template>
  <h1>设备</h1>
  <p class="sub">每台音箱一个 adapter，能力位决定它能做什么。缺 reference 的设备无法在放歌时插话。</p>

  <p v-if="error" class="fail">{{ error }}</p>
  <p v-if="!devices.length && !error" class="empty">
    还没有设备。编辑 <code>config.yaml</code> 的 <code>devices</code> 后重启。
  </p>

  <div v-if="devices.length" class="card">
    <h2>音量 <span class="tag">mysoftvol</span></h2>
    <div class="row">
      <span class="k">当前</span>
      <span class="v">{{ volume ?? '—' }} / 255 <span v-if="volumeNote" class="note">{{ volumeNote }}</span></span>
    </div>
    <div class="row">
      <span class="k">设置</span>
      <span class="v">
        <input type="range" min="20" max="255" :value="volume ?? 160" style="width: 220px; vertical-align: middle"
          @change="writeVolume(Number(($event.target as HTMLInputElement).value))" />
        <button style="margin-left: 8px" @click="readVolume">读取</button>
      </span>
    </div>
    <p class="note">
      这是唯一真旋钮（数字增益会被压缩器吃掉）。原厂会自己改写它，所以这里只显示刚读到的值；
      默认不接管（<code>devices[].volume: 0</code> 表示归原厂）。
    </p>
  </div>

  <div v-for="d in devices" :key="d.id" class="card">
    <h2>{{ d.room || d.id }} <span class="tag">{{ d.adapter }}</span></h2>
    <div class="row">
      <span class="k">status</span>
      <span class="v" :class="d.status === 'ready' ? 'ok' : 'fail'">{{ d.status }}</span>
    </div>
    <div class="row" v-if="d.error"><span class="k">error</span><span class="v fail">{{ d.error }}</span></div>
    <div class="row"><span class="k">id</span><span class="v">{{ d.id }}</span></div>
    <div class="row"><span class="k">host</span><span class="v">{{ d.host }}</span></div>
    <div class="row" v-if="d.model"><span class="k">model</span><span class="v">{{ d.model }}</span></div>
    <div class="row" v-if="d.transport !== 'unknown'"><span class="k">transport</span><span class="v">{{ d.transport }}</span></div>
    <div class="row" v-if="d.capabilities.length">
      <span class="k">capabilities</span>
      <span class="v"><span v-for="c in d.capabilities" :key="c" class="tag">{{ c }}</span></span>
    </div>
    <div class="row" v-if="d.capabilities.length">
      <span class="k">barge-in</span>
      <span class="v" :class="d.missing_for_barge_in.length ? 'warn' : 'ok'">
        {{ d.missing_for_barge_in.length ? '不可用，缺 ' + d.missing_for_barge_in.join(', ') : '可用' }}
      </span>
    </div>
  </div>
</template>
