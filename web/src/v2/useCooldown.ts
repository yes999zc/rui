import { computed, onBeforeUnmount, ref } from 'vue'

/**
 * Server-advertised 429 cooldown shared by the M5 write buttons.
 *
 * `Retry-After` (or `details.retry_after_s`) is the source; the countdown only
 * disables the submit/restart buttons, it never retries.
 */
export function useCooldown() {
  const seconds = ref(0)
  let timer: number | null = null

  function clearTimer(): void {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  function start(value: number): void {
    clearTimer()
    seconds.value = Math.max(0, Math.ceil(value))
    if (seconds.value <= 0) return
    timer = window.setInterval(() => {
      seconds.value -= 1
      if (seconds.value <= 0) clearTimer()
    }, 1000)
  }

  function clear(): void {
    clearTimer()
    seconds.value = 0
  }

  onBeforeUnmount(clearTimer)

  return {
    seconds,
    active: computed(() => seconds.value > 0),
    start,
    clear,
  }
}
