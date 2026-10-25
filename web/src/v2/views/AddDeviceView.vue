<script setup lang="ts">
/**
 * `/v2/devices/add` — M6 add wizard.
 *
 * The eight-step UI is the same engine as `/v2/setup`; the server plan decides
 * which steps are available and this entry never falls back to `device.update`.
 */
import SetupWizard from '../components/setup/SetupWizard.vue'
</script>

<template>
  <SetupWizard mode="add" />
</template>
