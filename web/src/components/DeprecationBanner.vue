<script setup lang="ts">
/**
 * 0.7.8 D: the old shell is deprecated. `web.console: v2` made `/v2/` the entry
 * point and moved this bundle to `/legacy`; the shell says so at the top instead
 * of relying on the release notes. Copy lives in i18n (`shell.deprecated.*`) so
 * both languages are shipped and type-checked; 0.8.0 deletes the whole shell.
 */
import { t } from '../i18n'

const NEW_CONSOLE = '/v2/'
</script>

<template>
  <section class="deprecation-banner" role="status" data-testid="legacy-deprecation">
    <span class="deprecation-tag">{{ t('shell.deprecated.tag') }}</span>
    <p class="deprecation-body">{{ t('shell.deprecated.body') }}</p>
    <a class="btn btn-primary deprecation-cta" :href="NEW_CONSOLE">{{ t('shell.deprecated.cta') }}</a>
  </section>
</template>

<style scoped>
.deprecation-banner {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin: 0;
  padding: 9px 48px;
  border-bottom: 1px solid var(--warn);
  background: var(--warn-soft);
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.45;
}

.deprecation-tag {
  flex: none;
  padding: 1px 7px;
  border: 1px solid var(--warn);
  border-radius: 999px;
  color: var(--warn);
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  white-space: nowrap;
}

.deprecation-body {
  margin: 0;
  flex: 1 1 260px;
  min-width: 0;
}

.deprecation-cta {
  flex: none;
  min-height: 30px;
  padding: 4px 12px;
  font-size: 12px;
}

@media (max-width: 720px) {
  .deprecation-banner {
    padding: 9px 16px;
  }

  .deprecation-cta {
    min-height: 44px;
  }
}
</style>
