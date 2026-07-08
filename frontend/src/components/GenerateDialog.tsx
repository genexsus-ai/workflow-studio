import { useState } from 'react'

import { generateWorkflow, type GenerateResult } from '../api'
import type { WorkflowDoc } from '../types'

interface GenerateDialogProps {
  open: boolean
  onClose: () => void
  onGenerated: (result: GenerateResult) => void
  /** Current canvas document; enables "refine" mode when it has nodes. */
  currentDoc: WorkflowDoc | null
}

const STAGE_LABELS: Record<string, string> = {
  planning: 'Planner: designing the workflow…',
  planned: 'Planner: plan ready',
  delegating: 'Delegator: routing work to specialists…',
  delegated: 'Delegator: work packets assigned',
  designing: 'Workers: designing agents and nodes…',
  designed: 'Workers: designs merged',
  reviewing: 'Reviewer: checking the plan…',
  reviewed: 'Reviewer: verdict in',
  compile_failed: 'Compilation failed — re-planning…',
  compiled: 'Workflow compiled',
}

function describe(event: { stage?: string; [key: string]: unknown }): string {
  const base = STAGE_LABELS[event.stage ?? ''] ?? event.stage ?? ''
  if (event.stage === 'planned' && typeof event.steps === 'number') {
    return `Planner: ${event.steps} step${event.steps === 1 ? '' : 's'} planned ("${String(event.name ?? '')}")`
  }
  if (event.stage === 'designing') {
    return `Worker ${String(event.worker ?? '')}: working on ${String(event.packet ?? '')}`
  }
  if (event.stage === 'reviewed') {
    return event.approved
      ? 'Reviewer: approved'
      : `Reviewer: rejected (${(event.issues as string[] | undefined)?.length ?? 0} issues)`
  }
  return base
}

export function GenerateDialog({ open, onClose, onGenerated, currentDoc }: GenerateDialogProps) {
  const [prompt, setPrompt] = useState('')
  const [name, setName] = useState('')
  const [crew, setCrew] = useState(true)
  const [refine, setRefine] = useState(false)
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  const canRefine = (currentDoc?.nodes.length ?? 0) > 0

  const generate = async () => {
    if (!prompt.trim() || busy) return
    setBusy(true)
    setError(null)
    setLog([])
    try {
      const result = await generateWorkflow(
        prompt.trim(),
        crew,
        (event) => {
          if (event.event === 'progress') {
            const line = describe(event)
            if (line) setLog((current) => [...current, line])
          }
        },
        undefined,
        refine && canRefine && currentDoc ? currentDoc : undefined,
        // Refine mode keeps the workflow's current name; otherwise use the
        // optional name field (the AI names it when left empty).
        refine && canRefine && currentDoc ? currentDoc.name : name.trim() || undefined,
      )
      onGenerated(result)
      setPrompt('')
      setName('')
      setLog([])
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="generate-overlay" onClick={busy ? undefined : onClose}>
      <div className="generate-dialog" onClick={(event) => event.stopPropagation()}>
        <h2>Generate workflow with AI</h2>
        <p className="generate-hint">
          Describe what the workflow should do; a draft appears on the canvas for you to
          review, edit, and save.
        </p>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder={
            refine && canRefine
              ? 'e.g. Also post the summary to Slack, and run every morning'
              : 'e.g. When a support ticket arrives, classify it, answer routine ones, and escalate urgent ones to Slack'
          }
          rows={4}
          disabled={busy}
          autoFocus
        />
        {!(refine && canRefine) && (
          <input
            className="generate-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Workflow name (optional — the AI picks one if empty)"
            disabled={busy}
          />
        )}
        {canRefine && (
          <label className="generate-crew">
            <input
              type="checkbox"
              checked={refine}
              onChange={(event) => setRefine(event.target.checked)}
              disabled={busy}
            />
            Refine the current canvas workflow instead of starting fresh
          </label>
        )}
        <label className="generate-crew">
          <input
            type="checkbox"
            checked={crew}
            onChange={(event) => setCrew(event.target.checked)}
            disabled={busy}
          />
          Use the multi-agent crew (planner, delegator, designers, reviewer)
        </label>
        {log.length > 0 && (
          <ul className="generate-log">
            {log.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        )}
        {error && <div className="generate-error">{error}</div>}
        <div className="generate-actions">
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="primary" onClick={generate} disabled={busy || !prompt.trim()}>
            {busy ? 'Generating…' : '✨ Generate'}
          </button>
        </div>
      </div>
    </div>
  )
}
