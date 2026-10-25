<script setup lang="ts">
/**
 * `/v2/setup` — M6 first-run wizard.
 *
 * Same server plan as the add flow, requested with the first-run mode derived by
 * the backend from an empty house. When the house already has devices the plan
 * comes back as `add`; the page says so and keeps using only plan actions.
 */
import SetupWizard from '../components/setup/SetupWizard.vue'
</script>

<template>
  <SetupWizard mode="first-run" />
</template>
