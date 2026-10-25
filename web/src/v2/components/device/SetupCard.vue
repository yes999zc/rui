<script setup lang="ts">
/** Setup/test evidence summary (read-only projection of `device.setup`). */
import type { DeviceSetup } from '../../api'
import { formatIso } from '../../format'
import { t } from '../../i18n'

defineProps<{ setup: DeviceSetup }>()

function boolText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.common.na')
  return value ? t('v2.common.yes') : t('v2.common.no')
}
</script>

<template>
  <section v-if="setup" class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.detail.setup') }}</h2>
    <dl class="v2-kv">
      <div>
        <dt>{{ t('v2.detail.setupConfigured') }}</dt>
        <dd>{{ boolText(setup.configured) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.setupLastTest') }}</dt>
        <dd>{{ boolText(setup.last_test_ok) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.setupLastTestAt') }}</dt>
        <dd>
          {{
            setup.last_test_at
              ? formatIso(setup.last_test_at)
              : t('v2.detail.setupNeverTested')
          }}
        </dd>
      </div>
    </dl>
  </section>
</template>
