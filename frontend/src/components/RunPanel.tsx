import { useEffect, useState } from 'react'

import { submitHumanInput } from '../api'
import type { ModelOption, RunEvent } from '../types'

interface RunPanelProps {
  open: boolean
  running: boolean
  events: RunEvent[]
  error: string | null
  models: ModelOption[]
  /** Input skeleton derived from {{ input.* }} references in the workflow. */
  suggestedInput: Record<string, unknown> | null
  /** Sample input pinned on the workflow (n8n-style); prefills the run input. */
  pinnedInput: Record<string, unknown> | null
  onPin: (input: Record<string, unknown> | null) => void
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
  pinnedInput,
  onPin,
  onClose,
  onStart,
}: RunPanelProps) {
  const [inputText, setInputText] = useState(GENERIC_INPUT)
  const [inputError, setInputError] = useState<string | null>(null)
  const [inputWarning, setInputWarning] = useState<string | null>(null)
  const [modelOverride, setModelOverride] = useState('')

  // Each time the panel opens, pre-fill with pinned data if present, else the
  // fields this workflow references
  useEffect(() => {
    if (!open) return
    setInputError(null)
    setInputWarning(null)
    setInputText(
      pinnedInput
        ? JSON.stringify(pinnedInput, null, 2)
        : suggestedInput && Object.keys(suggestedInput).length > 0
          ? JSON.stringify(suggestedInput, null, 2)
          : GENERIC_INPUT,
    )
  }, [open, suggestedInput, pinnedInput])

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

  // Waiting human nodes, derived from the event stream: a request is pending
  // until its response is delivered or the run reaches a terminal state.
  const runId = events.find((event) => event.event === 'started')?.data.run_id as
    | string
    | undefined
  const answered = new Set(
    events
      .filter((event) => event.event === 'human_input_received')
      .map((event) => String(event.data.node_id)),
  )
  const pendingInput = finalEvent
    ? []
    : events
        .filter((event) => event.event === 'human_input_required')
        .map((event) => ({
          nodeId: String(event.data.node_id),
          prompt: String(event.data.prompt ?? 'Input required'),
        }))
        .filter((request) => !answered.has(request.nodeId))

  const respond = async (nodeId: string, response: unknown) => {
    if (!runId) return
    try {
      await submitHumanInput(runId, nodeId, response)
      setInputError(null)
    } catch (err) {
      setInputError(`Could not deliver response: ${(err as Error).message}`)
    }
  }

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
      <div className="pin-actions">
        <button
          disabled={running}
          title="Save this input on the workflow as sample data (save workflow to persist)"
          onClick={() => {
            try {
              const parsed = inputText.trim() ? JSON.parse(inputText) : {}
              onPin(parsed)
              setInputError(null)
            } catch (err) {
              setInputError(`Cannot pin invalid JSON: ${(err as Error).message}`)
            }
          }}
        >
          📌 Pin input
        </button>
        {pinnedInput && (
          <>
            <button
              disabled={running}
              onClick={() => setInputText(JSON.stringify(pinnedInput, null, 2))}
            >
              Reset to pinned
            </button>
            <button disabled={running} onClick={() => onPin(null)}>
              Unpin
            </button>
          </>
        )}
      </div>
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

      {pendingInput.map((request) => (
        <HumanInputPrompt
          key={request.nodeId}
          nodeId={request.nodeId}
          prompt={request.prompt}
          onRespond={respond}
        />
      ))}

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

function HumanInputPrompt({
  nodeId,
  prompt,
  onRespond,
}: {
  nodeId: string
  prompt: string
  onRespond: (nodeId: string, response: unknown) => Promise<void>
}) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)

  const send = async (response: unknown) => {
    setSending(true)
    try {
      await onRespond(nodeId, response)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="human-input-prompt">
      <p>
        🙋 <strong>{nodeId}</strong>: {prompt}
      </p>
      <textarea
        rows={2}
        value={text}
        placeholder="Type a response (JSON or plain text)…"
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="human-input-actions">
        <button
          className="primary"
          disabled={sending}
          onClick={() => {
            let response: unknown = text
            try {
              response = JSON.parse(text)
            } catch {
              /* plain text response */
            }
            void send(response)
          }}
        >
          Send
        </button>
        <button disabled={sending} onClick={() => void send('approved')}>
          ✓ Approve
        </button>
        <button disabled={sending} onClick={() => void send('rejected')}>
          ✗ Reject
        </button>
      </div>
    </div>
  )
}
