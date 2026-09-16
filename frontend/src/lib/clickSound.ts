const KEY_ENABLED = 'audit_platform_click_sound'
const KEY_VOLUME = 'audit_platform_click_sound_volume' // 0-100
const KEY_PRESET = 'audit_platform_click_sound_preset'

/** A few distinct short synthesized tones — no audio files to ship or
 * load, just different oscillator waveforms/pitches/lengths so each one
 * is easy to tell apart. */
export const SOUND_PRESETS = {
  classic: { label: 'Classic click', type: 'square' as OscillatorType, frequency: 1000, duration: 0.03 },
  soft: { label: 'Soft pop', type: 'sine' as OscillatorType, frequency: 600, duration: 0.05 },
  tick: { label: 'Tick', type: 'triangle' as OscillatorType, frequency: 1400, duration: 0.02 },
  blip: { label: 'Blip', type: 'sawtooth' as OscillatorType, frequency: 800, duration: 0.035 },
}

export type SoundPresetKey = keyof typeof SOUND_PRESETS

export function isClickSoundEnabled(): boolean {
  try {
    return localStorage.getItem(KEY_ENABLED) !== 'off'
  } catch {
    return true
  }
}

export function setClickSoundEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(KEY_ENABLED, enabled ? 'on' : 'off')
  } catch {
    // Private browsing / storage blocked — the toggle just won't persist across reloads.
  }
  document.dispatchEvent(new CustomEvent('click-sound-changed'))
}

export function getClickVolume(): number {
  try {
    const raw = localStorage.getItem(KEY_VOLUME)
    const n = raw === null ? 60 : Number(raw)
    return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : 60
  } catch {
    return 60
  }
}

export function setClickVolume(volume: number): void {
  try {
    localStorage.setItem(KEY_VOLUME, String(Math.round(Math.min(100, Math.max(0, volume)))))
  } catch {
    // ignore — see setClickSoundEnabled
  }
  document.dispatchEvent(new CustomEvent('click-sound-changed'))
}

export function getClickPreset(): SoundPresetKey {
  try {
    const raw = localStorage.getItem(KEY_PRESET) as SoundPresetKey | null
    return raw && raw in SOUND_PRESETS ? raw : 'classic'
  } catch {
    return 'classic'
  }
}

export function setClickPreset(preset: SoundPresetKey): void {
  try {
    localStorage.setItem(KEY_PRESET, preset)
  } catch {
    // ignore — see setClickSoundEnabled
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

/** Plays whichever preset/volume the user currently has selected. Exported
 * so the settings panel can offer a "Test" button, not just the global
 * click listener. */
export function playClickSound(): void {
  const volume = getClickVolume()
  if (volume <= 0) return
  const ctx = getAudioContext()
  if (!ctx) return
  if (ctx.state === 'suspended') void ctx.resume()

  const preset = SOUND_PRESETS[getClickPreset()]
  const peakGain = 0.18 * (volume / 100)
  const oscillator = ctx.createOscillator()
  const gain = ctx.createGain()
  oscillator.type = preset.type
  oscillator.frequency.value = preset.frequency
  gain.gain.setValueAtTime(peakGain, ctx.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + preset.duration)
  oscillator.connect(gain)
  gain.connect(ctx.destination)
  oscillator.start()
  oscillator.stop(ctx.currentTime + preset.duration)
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
      playClickSound()
    },
    { capture: true },
  )
}
