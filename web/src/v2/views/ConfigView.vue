<script setup lang="ts">
import { RouterLink } from 'vue-router'
import StateBlock from '../components/StateBlock.vue'
import { meta } from '../meta'
import { t } from '../i18n'

const contract = meta.value?.contract ?? ''
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ contract || t('v2.app.kicker') }}</span>
        <h1>{{ t('v2.config.title') }}</h1>
        <p class="lede">{{ t('v2.config.lede') }}</p>
      </div>
    </header>

    <StateBlock kind="unavailable" :title="t('v2.config.stub')" :hint="t('v2.config.stubHint')">
      <RouterLink class="btn" to="/devices">{{ t('v2.config.openDevices') }}</RouterLink>
      <RouterLink class="btn" to="/rooms">{{ t('v2.config.openRooms') }}</RouterLink>
      <a class="btn btn-primary" href="/">{{ t('v2.config.openLegacy') }}</a>
    </StateBlock>
  </div>
</template>
