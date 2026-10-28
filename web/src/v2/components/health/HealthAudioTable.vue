<script setup lang="ts">
/** The audio-plane table from `/health`; rows arrive already severity-sorted. */
import { RouterLink } from 'vue-router'
import type { HealthAudioDevice } from '../../api'
import { audioSeverity, speakerText } from '../../health'
import { t } from '../../i18n'

defineProps<{
  rows: HealthAudioDevice[]
}>()
</script>

<template>
  <section class="v2-card" data-testid="health-audio">
    <header class="v2-card-head">
      <h2 class="v2-card-title">{{ t('v2.health.audio.title') }}</h2>
      <span class="meta">{{ t('v2.common.items', { n: rows.length }) }}</span>
    </header>
    <p class="v2-card-lede">{{ t('v2.health.audio.lede') }}</p>
    <div v-if="rows.length" class="v2-health-table-wrap">
      <table class="table v2-health-table">
        <thead>
          <tr>
            <th>{{ t('v2.health.audio.device') }}</th>
            <th>{{ t('v2.health.audio.plane') }}</th>
            <th>{{ t('v2.health.audio.speaker') }}</th>
            <th>{{ t('v2.health.audio.muted') }}</th>
            <th>{{ t('v2.health.audio.wake') }}</th>
            <th>{{ t('v2.health.audio.restarts') }}</th>
            <th>{{ t('v2.health.audio.error') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.id"
            :class="{ 'is-abnormal': Boolean(audioSeverity(row)) }"
            :data-testid="'health-audio-row'"
          >
            <td>
              <RouterLink class="mono" :to="`/devices/${encodeURIComponent(row.id)}`">
                {{ row.id }}
              </RouterLink>
              <span class="meta">{{ row.room || t('v2.common.na') }}</span>
            </td>
            <td>
              <span class="badge" :class="row.running ? 'badge-ok' : 'badge-fail'">
                {{ row.running ? t('v2.health.plane.running') : t('v2.health.plane.stopped') }}
              </span>
            </td>
            <td>{{ speakerText(row.speaker_connected) }}</td>
            <td>
              <span v-if="row.muted" class="badge badge-warn">
                {{ t('v2.health.audio.muted') }}
              </span>
              <span v-else class="meta">{{ t('v2.common.no') }}</span>
            </td>
            <td class="mono">
              {{ row.wake_admitted }} / {{ row.wake_candidates }}
              <span v-if="row.utterances" class="meta">
                · {{ t('v2.health.audio.utterances', { n: row.utterances }) }}
              </span>
            </td>
            <td class="mono">{{ row.restarts }}</td>
            <td>
              <span v-if="row.error" class="v2-warn-text mono v2-break">{{ row.error }}</span>
              <span v-else class="meta">{{ t('v2.common.na') }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="note">{{ t('v2.health.audio.empty') }}</p>
  </section>
</template>
