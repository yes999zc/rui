import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { initLocale } from './i18n'
import { initTheme } from '../theme'
import '../style.css'
import './style.css'

// Shared appearance/language preferences (same localStorage keys as the legacy
// console); the inline bootstrap in v2.html applies them before first paint.
initTheme()
initLocale()

const app = createApp(App).use(router)
// Wait for the initial navigation so a deep link renders the right page and the
// shell does not flash the devices route on the way to `/run` or `/config`.
void router.isReady().then(() => app.mount('#v2-app'))
