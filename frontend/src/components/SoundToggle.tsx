import { useEffect, useRef, useState } from 'react'
import {
  SOUND_PRESETS,
  getClickPreset,
  getClickVolume,
  isClickSoundEnabled,
  playClickSound,
  setClickPreset,
  setClickSoundEnabled,
  setClickVolume,
  type SoundPresetKey,
} from '../lib/clickSound'

export function SoundToggle() {
  const [enabled, setEnabled] = useState(isClickSoundEnabled())
  const [preset, setPreset] = useState(getClickPreset())
  const [volume, setVolume] = useState(getClickVolume())
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const sync = () => {
      setEnabled(isClickSoundEnabled())
      setPreset(getClickPreset())
      setVolume(getClickVolume())
    }
    document.addEventListener('click-sound-changed', sync)
    return () => document.removeEventListener('click-sound-changed', sync)
  }, [])

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title="Click sound settings"
        className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink-soft hover:bg-bg"
      >
        {enabled ? '🔊 Sound on' : '🔇 Sound off'}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-64 rounded-lg border border-line bg-surface p-3 shadow-lg">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-ink-soft">Button click sound</span>
            <button
              onClick={() => setClickSoundEnabled(!enabled)}
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${enabled ? 'bg-accent-soft text-accent-ink' : 'bg-bg text-ink-soft'}`}
            >
              {enabled ? 'On' : 'Off'}
            </button>
          </div>

          <div className="mt-3">
            <label className="text-xs font-medium text-ink">Sound</label>
            <div className="mt-1 space-y-1">
              {(Object.keys(SOUND_PRESETS) as SoundPresetKey[]).map((key) => (
                <label key={key} className="flex items-center gap-2 text-xs text-ink">
                  <input
                    type="radio"
                    name="click-sound-preset"
                    checked={preset === key}
                    onChange={() => {
                      setClickPreset(key)
                      playClickSound()
                    }}
                  />
                  {SOUND_PRESETS[key].label}
                </label>
              ))}
            </div>
          </div>

          <div className="mt-3">
            <label className="text-xs font-medium text-ink">Volume — {volume}%</label>
            <input
              type="range"
              min={0}
              max={100}
              value={volume}
              onChange={(e) => setClickVolume(Number(e.target.value))}
              onMouseUp={() => playClickSound()}
              onTouchEnd={() => playClickSound()}
              className="mt-1 w-full"
            />
          </div>

          <button
            onClick={playClickSound}
            className="mt-3 w-full rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg"
          >
            Test sound
          </button>
        </div>
      )}
    </div>
  )
}
