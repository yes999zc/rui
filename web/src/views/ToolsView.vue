<script setup lang="ts">
/**
 * A read-only reference page: the tools the model can actually see, plus the
 * exposure policy. Reachable from the Tools config panel; not in the main nav
 * because it is a lookup, not a place you visit every day.
 */
import { onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { api, type Config } from '../api'
import { t } from '../i18n'

const names = ref<string[]>([])
const cfg = ref<Config | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    const [tools, config] = await Promise.all([api.tools(), api.config()])
    names.value = tools.names
    cfg.value = config
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
})
</script>

<template>
  <header class="page-head">
    <div>
      <div class="kicker">{{ t('tools.kicker') }}</div>
      <h1>{{ t('tools.title') }}</h1>
      <p class="lede">{{ t('tools.lede') }}</p>
    </div>
    <div class="page-head-side">
      <span class="badge badge-ok">{{ t('tools.registered') }} · {{ names.length }}</span>
    </div>
  </header>

  <div v-if="error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ error }}</span>
  </div>

  <section v-if="cfg" class="block">
    <div class="block-head">
      <h2>{{ t('tools.policy') }}</h2>
    </div>
    <div class="row">
      <span class="k">{{ t('tools.expose') }}</span>
      <span class="v mono">{{ cfg.tools.expose }}</span>
    </div>
    <div class="row">
      <span class="k">{{ t('tools.whitelist') }}</span>
      <span class="v mono">{{ cfg.tools.whitelist.length }}</span>
    </div>
    <div class="row">
      <span class="k">{{ t('tools.mcpServers') }}</span>
      <span class="v mono">{{ cfg.tools.mcp_servers.length }}</span>
    </div>
    <p class="field-hint" style="margin-top: var(--s3)">{{ t('tools.note') }}</p>
  </section>

  <section class="block">
    <div class="block-head">
      <h2>{{ t('tools.registered') }}</h2>
      <span class="meta">{{ names.length }}</span>
    </div>
    <p v-if="!names.length" class="empty">{{ t('tools.empty') }}</p>
    <div class="tool-grid">
      <code v-for="name in names" :key="name" class="tool-name mono">{{ name }}</code>
    </div>
  </section>
</template>

<style scoped>
.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}

.tool-name {
  background: var(--surface);
  padding: 7px 10px;
  font-size: 11.5px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .tool-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
