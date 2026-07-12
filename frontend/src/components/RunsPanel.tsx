import { useCallback, useEffect, useState } from 'react'

import { cancelRun, deleteRun, getRun, listRuns, rerunRun, retryRunFromFailure } from '../api'
import type { RunRecord } from '../types'

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString()
}

const ACTIVE_STATUSES = new Set(['queued', 'running'])

function statusClass(status: string): string {
  if (status === 'success') return 'status-completed'
  if (ACTIVE_STATUSES.has(status)) return 'status-running-chip'
  if (status === 'cancelled' || status === 'interrupted') return 'status-skipped'
  return 'status-failed'
}

export function RunsPanel({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [detail, setDetail] = useState<RunRecord | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    listRuns().then(setRuns).catch(() => setRuns([]))
  }, [])

  useEffect(refresh, [refresh, refreshKey])

  // auto-refresh while anything is active
  useEffect(() => {
    if (!runs.some((run) => ACTIVE_STATUSES.has(run.status))) return
    const timer = setInterval(refresh, 1500)
    return () => clearInterval(timer)
  }, [runs, refresh])

  const openDetail = async (runId: string) => {
    try {
      setDetail(await getRun(runId))
    } catch {
      /* ignore */
    }
  }

  const onCancel = async (runId: string) => {
    setBusy(true)
    try {
      await cancelRun(runId)
      refresh()
      if (detail?.run_id === runId) await openDetail(runId)
    } finally {
      setBusy(false)
    }
  }

  const onRerun = async (runId: string) => {
    setBusy(true)
    try {
      await rerunRun(runId)
      refresh()
    } finally {
      setBusy(false)
    }
  }

  const onRetryFromFailure = async (runId: string) => {
    setBusy(true)
    try {
      await retryRunFromFailure(runId)
      refresh()
    } finally {
      setBusy(false)
    }
  }

  const onDelete = async (runId: string) => {
    setBusy(true)
    try {
      await deleteRun(runId)
      if (detail?.run_id === runId) setDetail(null)
      refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="rail-section">
      <summary>
        Run history {runs.length > 0 && <span className="runs-count">({runs.length})</span>}
      </summary>
      <button className="refresh-button" onClick={refresh}>
        ↻ Refresh
      </button>
      {runs.length === 0 ? (
        <p className="config-subtitle">No runs yet.</p>
      ) : (
        <table className="runs-table">
          <thead>
            <tr>
              <th>Workflow</th>
              <th>Status</th>
              <th>Via</th>
              <th>At</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.run_id}
                title={run.error ?? run.run_id}
                className={detail?.run_id === run.run_id ? 'run-row-selected' : 'run-row'}
                onClick={() => openDetail(run.run_id)}
              >
                <td>{run.workflow}</td>
                <td>
                  <span className={`node-output-status ${statusClass(run.status)}`}>
                    {run.status}
                  </span>
                </td>
                <td>{run.metadata?.trigger ?? 'manual'}</td>
                <td>{formatTime(run.started_at)}</td>
                <td onClick={(event) => event.stopPropagation()}>
                  {ACTIVE_STATUSES.has(run.status) ? (
                    <button className="danger" disabled={busy} onClick={() => onCancel(run.run_id)}>
                      ✕
                    </button>
                  ) : (
                    <div className="run-actions">
                      <button
                        className="refresh-button rerun-button"
                        disabled={busy}
                        title="Re-run with the same workflow + input"
                        onClick={() => onRerun(run.run_id)}
                      >
                        ↻
                      </button>
                      {run.status === 'error' && (
                        <button
                          className="refresh-button rerun-button"
                          disabled={busy}
                          title="Retry from the failed node — successful nodes replay their previous outputs"
                          onClick={() => onRetryFromFailure(run.run_id)}
                        >
                          ⤻
                        </button>
                      )}
                      <button
                        className="danger"
                        disabled={busy}
                        title="Delete this run from history"
                        onClick={() => onDelete(run.run_id)}
                      >
                        🗑
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {detail && (
        <div className="run-detail">
          <div className="run-detail-header">
            <strong>{detail.workflow}</strong>
            <button onClick={() => setDetail(null)}>✕</button>
          </div>
          <p className="config-subtitle">
            {detail.run_id} — {detail.status}
            {detail.error ? ` — ${detail.error}` : ''}
          </p>
          {Object.entries(detail.result?.node_results ?? {}).map(([nodeId, entry]) => (
            <details className="run-detail-node" key={nodeId}>
              <summary>
                <code>{nodeId}</code>{' '}
                <span className={`node-output-status ${statusClass(entry.status === 'completed' ? 'success' : entry.status)}`}>
                  {entry.status}
                </span>
                {entry.duration_ms != null && (
                  <span className="node-output-duration"> {entry.duration_ms}ms</span>
                )}
              </summary>
              {entry.error && <p className="error-text">{entry.error}</p>}
              <pre>{JSON.stringify(entry.output, null, 2)}</pre>
            </details>
          ))}
          {!detail.result?.node_results && (
            <p className="config-subtitle">No per-node results recorded for this run.</p>
          )}
        </div>
      )}
    </details>
  )
}
