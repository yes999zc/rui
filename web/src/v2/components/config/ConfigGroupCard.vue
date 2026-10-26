<script setup lang="ts">
/**
 * One catalog group rendered as a card. Group order, title and hint come from
 * `/config/catalog`; fields are rendered in the server's order so a catalog
 * change is visible without touching the client.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { ConfigFieldSpec, ConfigGroupSpec } from '../../api'
import { pickLocalized, t } from '../../i18n'
import ConfigField from './ConfigField.vue'

const props = defineProps<{
  group: ConfigGroupSpec
  fields: ConfigFieldSpec[]
  draft: Record<string, unknown>
  errors: Record<string, string>
  changed: Set<string>
}>()

const emit = defineEmits<{
  update: [path: string, value: unknown]
}>()

// The page-level changed set spans every group; the badge is per group so a
// change in one card cannot advertise unsaved edits on another.
const groupChanged = computed(
  () => new Set(props.fields.filter((field) => props.changed.has(field.path)).map((field) => field.path)),
)
</script>

<template>
  <section
    class="v2-card v2-config-group"
    :data-group-id="group.id"
    :data-group-tier="group.tier"
    :aria-labelledby="`v2-config-group-${group.id}`"
  >
    <header class="v2-config-group-head">
      <div>
        <h2 :id="`v2-config-group-${group.id}`" class="v2-card-title">
          {{ pickLocalized(group.title) }}
          <span
            v-if="group.effect === 'restart'"
            class="badge badge-restart"
            data-testid="group-restart-badge"
          >
            <AppIcon name="refresh" :size="11" />
            {{ t('v2.config.effect.restart') }}
          </span>
          <span v-if="groupChanged.size" class="badge badge-save" data-testid="group-unsaved">
            {{ t('v2.config.unsavedCount', { n: groupChanged.size }) }}
          </span>
        </h2>
        <p class="v2-card-lede">{{ pickLocalized(group.hint) }}</p>
      </div>
      <span class="meta">{{ t('v2.common.items', { n: fields.length }) }}</span>
    </header>

    <div class="v2-config-fields">
      <ConfigField
        v-for="field in fields"
        :key="field.path"
        :field="field"
        :model-value="draft[field.path]"
        :error="errors[field.path] ?? ''"
        @update:model-value="emit('update', field.path, $event)"
      />
    </div>
  </section>
</template>
