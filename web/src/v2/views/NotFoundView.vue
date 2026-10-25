<script setup lang="ts">
import { RouterLink } from 'vue-router'
import StateBlock from '../components/StateBlock.vue'
import { t } from '../i18n'
</script>

<template>
  <div class="v2-page">
    <StateBlock kind="notfound" :title="t('v2.notFound.title')" :hint="t('v2.notFound.hint')">
      <RouterLink class="btn btn-primary" to="/devices">{{ t('v2.notFound.action') }}</RouterLink>
    </StateBlock>
  </div>
</template>
