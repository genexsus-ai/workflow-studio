import { useEffect } from 'react'

import type { StudioNode } from '../lib/translate'
import type { NodeResult } from '../types'

/** One upstream node feeding the selected node (an incoming edge's source). */
export interface UpstreamEntry {
  id: string
  label: string
  result?: NodeResult
}

interface NodeIOViewProps {
  node: StudioNode
  /** Sources of the node's incoming edges, with their last-run results. */
  upstream: UpstreamEntry[]
  /** The run's input JSON — the "input" for entry nodes with no upstream. */
  workflowInput: Record<string, unknown> | null
  result?: NodeResult
  onClose: () => void
}

/**
 * n8n-style node detail view: INPUT (what flowed in from upstream nodes, or
 * the workflow input for entry nodes) side by side with OUTPUT (what this
 * node produced on the last run).
 */
export function NodeIOView({ node, upstream, workflowInput, result, onClose }: NodeIOViewProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="node-io-overlay" onClick={onClose}>
      <div className="node-io-dialog" onClick={(event) => event.stopPropagation()}>
        <div className="node-io-header">
          <h2>
            {node.data.label}
            <span className="node-io-type">{String(node.data.nodeType)}</span>
            {result && (
              <span className={`node-output-status status-${result.status}`}>{result.status}</span>
            )}
            {result?.duration_ms != null && (
              <span className="node-output-duration">{result.duration_ms}ms</span>
            )}
          </h2>
          <button onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>

        <div className="node-io-columns">
          <div className="node-io-col">
            <h3>Input</h3>
            {upstream.length > 0 ? (
              upstream.map((entry) => (
                <div className="node-io-block" key={entry.id}>
                  <div className="node-io-block-title">
                    <code>{entry.id}</code>
                    {entry.label !== entry.id && <span> — {entry.label}</span>}
                    {entry.result && (
                      <span className={`node-output-status status-${entry.result.status}`}>
                        {entry.result.status}
                      </span>
                    )}
                  </div>
                  {entry.result ? (
                    <pre>{JSON.stringify(entry.result.output, null, 2)}</pre>
                  ) : (
                    <p className="node-io-empty">No data yet — run the workflow to see what this node receives.</p>
                  )}
                </div>
              ))
            ) : workflowInput ? (
              <div className="node-io-block">
                <div className="node-io-block-title">
                  <code>input</code>
                  <span> — workflow input</span>
                </div>
                <pre>{JSON.stringify(workflowInput, null, 2)}</pre>
              </div>
            ) : (
              <p className="node-io-empty">
                This is an entry node — its input is the workflow's run input. Start a run to see it here.
              </p>
            )}
            {upstream.length > 0 && (
              <p className="node-io-hint">
                Reference these values with <code>{'{{ node_id.data.result }}'}</code> expressions.
              </p>
            )}
          </div>

          <div className="node-io-col">
            <h3>Output</h3>
            {result ? (
              <div className="node-io-block">
                {result.error && <p className="error-text">{result.error}</p>}
                <pre>{JSON.stringify(result.output, null, 2)}</pre>
              </div>
            ) : (
              <p className="node-io-empty">
                No output yet — run the workflow (or use the node test in the inspector) to see what this
                node produces.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
