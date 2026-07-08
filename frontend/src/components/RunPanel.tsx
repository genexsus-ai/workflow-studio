import { useEffect, useState } from 'react'

import type { ModelOption, RunEvent } from '../types'

interface RunPanelProps {
  open: boolean
  running: boolean
  events: RunEvent[]
  error: string | null
  models: ModelOption[]
  /** Input skeleton derived from {{ input.* }} references in the workflow. */
  suggestedInput: Record<string, unknown> | null
  onClose: () => void
  onStart: (input: Record<string, unknown>, modelOverride?: string) => void
}

const GENERIC_INPUT = '{\n  "task": "Say hello"\n}'

export function RunPanel({
  open,
  running,
  events,
  error,
  models,
  suggestedInput,
  onClose,
  onStart,
}: RunPanelProps) {
  const [inputText, setInputText] = useState(GENERIC_INPUT)
  const [inputError, setInputError] = useState<string | null>(null)
  const [inputWarning, setInputWarning] = useState<string | null>(null)
  const [modelOverride, setModelOverride] = useState('')

  // Each time the panel opens, pre-fill with the fields this workflow references
  useEffect(() => {
    if (!open) return
    setInputError(null)
    setInputWarning(null)
    setInputText(
      suggestedInput && Object.keys(suggestedInput).length > 0
        ? JSON.stringify(suggestedInput, null, 2)
        : GENERIC_INPUT,
    )
  }, [open, suggestedInput])

  if (!open) return null

  const requiredKeys = Object.keys(suggestedInput ?? {})

  const start = () => {
    try {
      const parsed = inputText.trim() ? JSON.parse(inputText) : {}
      setInputError(null)
      const missing = requiredKeys.filter(
        (key) => parsed[key] === undefined || parsed[key] === '',
      )
      setInputWarning(
        missing.length
          ? `The workflow references {{ input.${missing.join(' }}, {{ input.')} }} — missing or empty in this input.`
          : null,
      )
      onStart(parsed, modelOverride || undefined)
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
      {requiredKeys.length > 0 && (
        <p className="config-subtitle">
          This workflow expects: {requiredKeys.map((k) => `input.${k}`).join(', ')}
        </p>
      )}
      {inputError && <p className="error-text">{inputError}</p>}
      {inputWarning && <p className="warning-text">⚠ {inputWarning}</p>}
      <label className="field">
        <span>Model override (optional)</span>
        <select value={modelOverride} onChange={(event) => setModelOverride(event.target.value)}>
          <option value="">Use each agent's configured model</option>
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.label} ({model.provider})
            </option>
          ))}
        </select>
      </label>
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
