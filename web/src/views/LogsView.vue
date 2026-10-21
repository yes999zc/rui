<script setup lang="ts">
</script>

<template>
  <h1>日志</h1>
  <p class="sub">按设备分会话的实时日志。M2 接入 WebSocket 后在这里流式显示。</p>
  <div class="card">
    <p class="empty">暂无日志。当前进度可从 <code>docker compose logs -f house-ear</code> 查看。</p>
  </div>
</template>
