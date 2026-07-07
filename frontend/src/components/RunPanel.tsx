import { useState } from 'react'

import type { RunEvent } from '../types'

interface RunPanelProps {
  open: boolean
  running: boolean
  events: RunEvent[]
  error: string | null
  onClose: () => void
  onStart: (input: Record<string, unknown>) => void
}

export function RunPanel({ open, running, events, error, onClose, onStart }: RunPanelProps) {
  const [inputText, setInputText] = useState('{\n  "task": "Say hello"\n}')
  const [inputError, setInputError] = useState<string | null>(null)

  if (!open) return null

  const start = () => {
    try {
      const parsed = inputText.trim() ? JSON.parse(inputText) : {}
      setInputError(null)
      onStart(parsed)
    } catch (err) {
      setInputError(`Invalid JSON: ${(err as Error).message}`)
    }
  }

  const finalEvent = events.find((event) => event.event === 'complete' || event.event === 'error')
  const result = finalEvent?.data.result as Record<string, unknown> | undefined

  return (
    <section className="run-panel">
      <div className="run-panel-header">
        <h2>Run</h2>
        <button onClick={onClose}>✕</button>
      </div>

      <label className="field">
        <span>Input (JSON)</span>
        <textarea
          rows={4}
          value={inputText}
          spellCheck={false}
          onChange={(event) => setInputText(event.target.value)}
        />
      </label>
      {inputError && <p className="error-text">{inputError}</p>}
      <button className="primary" onClick={start} disabled={running}>
        {running ? 'Running…' : 'Start run'}
      </button>

      {error && <p className="error-text">{error}</p>}

      <div className="event-log">
        {events.map((event, index) => (
          <div key={index} className={`event event-${event.event}`}>
            {event.event === 'node' ? (
              <span>
                <code>{String(event.data.node_id)}</code> → {String(event.data.status)}
                {event.data.duration_ms != null ? ` (${String(event.data.duration_ms)}ms)` : ''}
                {event.data.error ? ` — ${String(event.data.error)}` : ''}
              </span>
            ) : (
              <span>
                <strong>{event.event}</strong>
                {event.event === 'error' ? ` — ${String(event.data.error ?? '')}` : ''}
              </span>
            )}
          </div>
        ))}
      </div>

      {finalEvent && finalEvent.event === 'complete' && result != null && (
        <details className="result-view" open>
          <summary>Final output</summary>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </details>
      )}
    </section>
  )
}
