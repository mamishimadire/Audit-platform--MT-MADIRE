const STORAGE_KEY = 'audit_platform_click_sound'

export function isClickSoundEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'off'
  } catch {
    return true
  }
}

export function setClickSoundEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, enabled ? 'on' : 'off')
  } catch {
    // Private browsing / storage blocked — the toggle just won't persist across reloads.
  }
  document.dispatchEvent(new CustomEvent('click-sound-changed'))
}

let audioContext: AudioContext | null = null

function getAudioContext(): AudioContext | null {
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  if (!audioContext) audioContext = new Ctor()
  return audioContext
}

/** A short synthesized click — no audio asset to ship or load, just a ~12ms
 * tone with a fast decay so it reads as a "click" rather than a beep. */
function playClick(): void {
  const ctx = getAudioContext()
  if (!ctx) return
  if (ctx.state === 'suspended') void ctx.resume()

  const oscillator = ctx.createOscillator()
  const gain = ctx.createGain()
  oscillator.type = 'square'
  oscillator.frequency.value = 1000
  gain.gain.setValueAtTime(0.06, ctx.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.03)
  oscillator.connect(gain)
  gain.connect(ctx.destination)
  oscillator.start()
  oscillator.stop(ctx.currentTime + 0.03)
}

let initialized = false

/** Attaches one app-wide listener instead of wiring an onClick into every
 * button component individually — plays on any button-like element
 * (button, [role=button], or a submit input) so the feature covers the
 * whole app without touching every existing button's markup. Call once
 * from the app root. */
export function initClickSound(): void {
  if (initialized) return
  initialized = true
  document.addEventListener(
    'click',
    (event) => {
      if (!isClickSoundEnabled()) return
      const target = event.target as Element | null
      const el = target?.closest('button, [role="button"], input[type="submit"]')
      if (!el || (el as HTMLButtonElement).disabled) return
      playClick()
    },
    { capture: true },
  )
}
