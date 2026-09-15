import { useEffect, useState } from 'react'
import { isClickSoundEnabled, setClickSoundEnabled } from '../lib/clickSound'

export function SoundToggle() {
  const [enabled, setEnabled] = useState(isClickSoundEnabled())

  useEffect(() => {
    const sync = () => setEnabled(isClickSoundEnabled())
    document.addEventListener('click-sound-changed', sync)
    return () => document.removeEventListener('click-sound-changed', sync)
  }, [])

  return (
    <button
      onClick={() => setClickSoundEnabled(!enabled)}
      title={enabled ? 'Click sound is on — click to mute' : 'Click sound is off — click to enable'}
      className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink-soft hover:bg-bg"
    >
      {enabled ? '🔊 Sound on' : '🔇 Sound off'}
    </button>
  )
}
