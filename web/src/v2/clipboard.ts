/**
 * Clipboard helper shared by the run pages.
 *
 * `navigator.clipboard` needs a secure context (the console is often reached
 * over plain HTTP on the LAN), so there is a `execCommand` fallback. Returning
 * a boolean lets the caller show "copied" only when it actually worked.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    try {
      area.select()
      return document.execCommand('copy')
    } finally {
      // `execCommand` can throw (or be missing); never leave the textarea in the
      // DOM — it would stack up invisibly across copy attempts.
      area.remove()
    }
  } catch {
    return false
  }
}
