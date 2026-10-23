import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'

const app = createApp(App).use(router)
// Wait for the initial navigation: App.vue's landing redirect must see the real
// route, not the transient '/' (deep links to a panel were bounced to /setup).
void router.isReady().then(() => app.mount('#app'))
