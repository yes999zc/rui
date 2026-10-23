import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { initLocale } from './i18n'
import { initTheme } from './theme'
import './style.css'

// Theme + language are applied before mount (index.html does the same early, so
// the first paint is already correct; this keeps state in sync afterwards).
initTheme()
initLocale()

const app = createApp(App).use(router)
// Wait for the initial navigation: App.vue's landing redirect must see the real
// route, not the transient '/' (deep links to a panel were bounced to /setup).
void router.isReady().then(() => app.mount('#app'))
