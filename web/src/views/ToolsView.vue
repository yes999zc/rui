<script setup lang="ts">
/**
 * A read-only reference page: the tools the model can actually see, plus the
 * exposure policy. Reached at `/tools` and as the 注册工具 tab of
 * `/panel/integrations`; `embedded` drops the page chrome there so the combined
 * page has one title (and one exposure editor, in its own tab).
 */
import { onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { api, type Config } from '../api'
import { exposeLabel, t, toolLabel } from '../i18n'

defineProps<{ embedded?: boolean }>()

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
  <header v-if="!embedded" class="page-head">
    <div>
      <div class="kicker">{{ t('tools.kicker') }}</div>
      <h1>{{ t('tools.title') }}</h1>
      <p class="lede">{{ t('tools.lede') }}</p>
    </div>
    <div class="page-head-side">
      <span class="badge badge-ok">{{ t('tools.registered') }} · {{ names.length }}</span>
    </div>
  </header>

  <div v-if="embedded" class="tools-embedded-meta">
    <span class="badge badge-ok">{{ t('tools.registered') }} · {{ names.length }}</span>
    <span class="meta">{{ t('tools.lede') }}</span>
  </div>

  <div v-if="error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ error }}</span>
  </div>

  <section v-if="cfg && !embedded" class="block">
    <div class="block-head">
      <h2>{{ t('tools.policy') }}</h2>
    </div>
    <div class="row">
      <span class="k">{{ t('tools.expose') }}</span>
      <span class="v">{{ exposeLabel(cfg.tools.expose) }}</span>
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
      <div v-for="name in names" :key="name" class="tool-name">
        <span class="tool-label">{{ toolLabel(name) }}</span>
        <code class="tool-id mono">{{ name }}</code>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tools-embedded-meta {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-bottom: var(--s4);
}

.tools-embedded-meta .meta {
  min-width: 0;
}

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
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-wrap: anywhere;
}

.tool-label {
  font-size: 12.5px;
  font-weight: 550;
  color: var(--ink);
}

.tool-id {
  font-size: 10px;
  color: var(--muted);
  background: none;
  padding: 0;
}

@media (max-width: 720px) {
  .tool-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
