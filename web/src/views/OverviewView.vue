<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AudioStatus } from '../api'

const health = ref<{ status: string; version: string; uptime_s: number; devices: number; device_errors: number } | null>(null)
const audio = ref<AudioStatus | null>(null)
const text = ref('现在几点？')
const reply = ref('')
const error = ref('')
const busy = ref(false)
const stopNote = ref('')

async function refresh() {
  try {
    health.value = await api.health()
    audio.value = await api.audio()
  } catch (e) {
    error.value = String(e)
  }
}

async function send() {
  if (!text.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  reply.value = ''
  stopNote.value = ''
  try {
    const result = await api.say(text.value.trim())
    reply.value = result.reply || '(空回复)'
    if (result.error) error.value = result.error
  } catch (e) {
    error.value = String(e)
  } finally {
    busy.value = false
    await refresh()
  }
}

async function stop() {
  try {
    await api.stopAudio()
    stopNote.value = '停止指令已发送'
  } catch (e) {
    error.value = String(e)
  }
}

onMounted(refresh)
</script>

<template>
  <h1>概览</h1>
  <p class="sub">
    服务的实时状态，以及一个不用开口就能测的入口：输入的每一句话都走完整的回合
    （工具、TTS、播放），和真说话完全同一条链路。
  </p>

  <p v-if="error" class="fail">{{ error }}</p>

  <div class="card">
    <h2>状态</h2>
    <div class="row"><span class="k">服务</span><span class="v" :class="health?.status === 'ok' ? 'ok' : 'fail'">{{ health?.status || '—' }}</span></div>
    <div class="row"><span class="k">版本</span><span class="v">{{ health?.version || '—' }}</span></div>
    <div class="row"><span class="k">运行时长</span><span class="v">{{ health ? Math.round(health.uptime_s / 60) + ' 分钟' : '—' }}</span></div>
    <div class="row">
      <span class="k">设备</span>
      <span class="v">{{ health?.devices ?? '—' }} 台<span v-if="health?.device_errors" class="warn"> · {{ health.device_errors }} 台不可用</span></span>
    </div>
    <div class="row">
      <span class="k">音箱</span>
      <span class="v" :class="audio?.metrics?.speaker_connected ? 'ok' : 'warn'">
        {{ audio?.metrics?.speaker_connected ? '已连接' : '未连接' }}
      </span>
    </div>
    <div class="row"><span class="k">喇叭占用</span><span class="v">{{ audio?.metrics?.output_owner || '空闲' }}</span></div>
    <div class="row"><span class="k">播放提前量</span><span class="v">{{ audio?.metrics?.playback_lead_ms ?? 0 }} ms</span></div>
    <div class="row"><span class="k">AEC</span><span class="v">{{ audio?.metrics?.aec_erle_db ?? '—' }} dB</span></div>
  </div>

  <div class="card">
    <h2>试一试</h2>
    <div class="row" style="align-items: center">
      <input v-model="text" style="flex: 1; margin-right: 8px" placeholder="像对音箱说话一样输入" @keyup.enter="send" />
      <button class="primary" :disabled="busy" @click="send">{{ busy ? '回合进行中…' : '发送' }}</button>
      <button style="margin-left: 8px" @click="stop">停止播放</button>
    </div>
    <p v-if="stopNote" class="note">{{ stopNote }}</p>
    <p v-if="reply" class="ok">答：{{ reply }}</p>
  </div>

  <p class="note">
    提示：这个入口不经过唤醒词与 ASR，适合验证「回答内容 / 工具是否调用 / 首字是否被吞」；
    唤醒灵敏度、打断与回声相关的结论仍要在真机上测。
  </p>
</template>
