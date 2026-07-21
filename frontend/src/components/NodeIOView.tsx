import { useEffect, useState } from 'react'

import { testNode } from '../api'
import { NodeParamsFields, type NodeParamsFieldsProps } from './NodeParamsFields'

import type { StudioNode } from '../lib/translate'
import type { NodeResult, NodeTestResult, NodeTypeDef } from '../types'

/** One upstream node feeding the selected node (an incoming edge's source). */
export interface UpstreamEntry {
  id: string
  label: string
  result?: NodeResult
}

type ParamsProps = Omit<NodeParamsFieldsProps, 'node' | 'def'>

interface NodeIOViewProps extends ParamsProps {
  node: StudioNode
  /** Sources of the node's incoming edges, with their last-run results. */
  upstream: UpstreamEntry[]
  /** The run's input JSON — the "input" for entry nodes with no upstream. */
  workflowInput: Record<string, unknown> | null
  result?: NodeResult
  nodeTypes: NodeTypeDef[]
  onClose: () => void
}

/** Flat objects render as n8n-style key/value rows; anything nested falls
 * back to pretty-printed JSON. */
export function KeyValueOutput({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data)
  const flat =
    entries.length > 0 &&
    entries.every(([, value]) => value === null || ['string', 'number', 'boolean'].includes(typeof value))
  if (!flat) return <pre>{JSON.stringify(data, null, 2)}</pre>
  return (
    <div className="kv-rows">
      {entries.map(([key, value]) => (
        <div className="kv-row" key={key}>
          <span className="kv-key">{key}</span>
          <span className="kv-value">{String(value)}</span>
        </div>
      ))}
    </div>
  )
}

const isFlat = (value: unknown) =>
  value === null || ['string', 'number', 'boolean'].includes(typeof value)

const TYPE_BADGES: Record<string, string> = {
  string: 'A',
  number: '#',
  boolean: '✓',
  object: '{}',
  array: '[]',
  null: '∅',
}

const typeOf = (value: unknown): string =>
  value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value

function SchemaView({ data, depth = 0 }: { data: unknown; depth?: number }) {
  if (typeof data !== 'object' || data === null) {
    return (
      <div className="schema-row">
        <span className="schema-badge">{TYPE_BADGES[typeOf(data)] ?? '?'}</span>
        <span className="schema-type">{typeOf(data)}</span>
      </div>
    )
  }
  const entries = Array.isArray(data)
    ? data.slice(0, 1).map((value, i) => [`[${i}]`, value] as const)
    : Object.entries(data)
  return (
    <div className="schema-rows">
      {entries.map(([key, value]) => (
        <div key={key} style={{ marginLeft: depth * 16 }}>
          <div className="schema-row">
            <span className="schema-badge">{TYPE_BADGES[typeOf(value)] ?? '?'}</span>
            <span className="schema-key">{key}</span>
            <span className="schema-type">{typeOf(value)}</span>
          </div>
          {typeof value === 'object' && value !== null && depth < 2 && (
            <SchemaView data={value} depth={depth + 1} />
          )}
        </div>
      ))}
    </div>
  )
}

function TableView({ data }: { data: unknown }) {
  // Array of flat objects -> a real table
  if (Array.isArray(data) && data.length > 0 && data.every((row) => typeof row === 'object' && row !== null && !Array.isArray(row))) {
    const rows = data.slice(0, 50) as Record<string, unknown>[]
    const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 8)
    return (
      <div className="output-table-wrap">
        <table className="output-table">
          <thead>
            <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c}>{isFlat(row[c]) ? String(row[c] ?? '') : JSON.stringify(row[c])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  // Single object -> n8n-style one-row table: field names as columns
  if (typeof data === 'object' && data !== null && !Array.isArray(data)) {
    const entries = Object.entries(data)
    if (entries.length === 0) return <p className="node-io-empty">Empty object.</p>
    return (
      <div className="output-table-wrap">
        <table className="output-table">
          <thead>
            <tr>
              {entries.map(([key]) => (
                <th key={key}>{key}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              {entries.map(([key, value]) => (
                <td key={key}>
                  {isFlat(value) ? String(value ?? '') : JSON.stringify(value).slice(0, 300)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    )
  }
  return <pre>{JSON.stringify(data, null, 2)}</pre>
}

/** n8n-style output panel: Table | JSON | Schema toggle over the data. */
export function OutputViewer({ data }: { data: unknown }) {
  const [view, setView] = useState<'table' | 'json' | 'schema'>('table')
  const items = Array.isArray(data) ? data.length : 1
  return (
    <div className="output-viewer">
      <div className="output-viewer-head">
        <span className="output-viewer-count">
          {items} item{items === 1 ? '' : 's'}
        </span>
        <div className="output-viewer-bar">
          {(['table', 'json', 'schema'] as const).map((mode) => (
            <button
              key={mode}
              className={view === mode ? 'active' : ''}
              onClick={() => setView(mode)}
            >
              {mode === 'json' ? 'JSON' : mode[0].toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {view === 'table' ? (
        <TableView data={data} />
      ) : view === 'json' ? (
        <pre className="output-viewer-json">{JSON.stringify(data, null, 2)}</pre>
      ) : (
        <SchemaView data={data} />
      )}
    </div>
  )
}

const TRIGGER_SOURCE_HINT: Record<string, string> = {
  form: 'A visitor submitting the hosted form fires this trigger — the submission becomes its output.',
  webhook: 'An incoming HTTP request fires this trigger — the request body becomes its output.',
  schedule: 'The schedule fires this trigger — its payload becomes the run input.',
  manual: 'Pressing ▶ Run fires this trigger — the run input you type becomes its output.',
}

/**
 * n8n-style node detail view: INPUT (what flowed in from upstream nodes, or
 * the workflow input for entry nodes) side by side with OUTPUT (what this
 * node produced on the last run). Trigger nodes show the event source as
 * input and the latest run's input (e.g. the form submission) as output.
 */
export function NodeIOView({
  node,
  upstream,
  workflowInput,
  result,
  nodeTypes,
  onClose,
  ...paramsProps
}: NodeIOViewProps) {
  const [testResult, setTestResult] = useState<NodeTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const def = nodeTypes.find((t) => t.type === node.data.nodeType)
  const workflowId = paramsProps.currentWorkflowId
  // Trigger nodes don't execute as steps; everything else can be run alone.
  const canExecute = node.data.nodeType !== 'trigger'

  const executeStep = async () => {
    if (!workflowId) return
    setTesting(true)
    setTestError(null)
    try {
      setTestResult(await testNode(workflowId, node.id))
    } catch (err) {
      setTestResult(null)
      setTestError((err as Error).message)
    } finally {
      setTesting(false)
    }
  }

  // Prefer a fresh Execute-step result; fall back to the last run's output.
  const shownOutput = testResult
    ? { output: testResult.output, status: testResult.status, error: testResult.error ?? undefined }
    : result

  return (
    <div className="node-io-overlay" onClick={onClose}>
      <div className="node-io-dialog node-io-dialog-3col" onClick={(event) => event.stopPropagation()}>
        <div className="node-io-header">
          <h2>
            {node.data.label}
            <span className="node-io-type">{String(node.data.nodeType)}</span>
            {shownOutput && (
              <span className={`node-output-status status-${shownOutput.status}`}>
                {shownOutput.status}
              </span>
            )}
            {result?.duration_ms != null && !testResult && (
              <span className="node-output-duration">{result.duration_ms}ms</span>
            )}
          </h2>
          {canExecute && (
            <button
              className="node-io-execute"
              onClick={executeStep}
              disabled={testing || !workflowId}
              title={
                workflowId
                  ? 'Run just this node with the current parameters'
                  : 'Save the workflow first'
              }
            >
              {testing ? 'Executing…' : '▶ Execute step'}
            </button>
          )}
          <button className="node-io-close-x" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>

        <div className="node-io-columns">
          <div className="node-io-col">
            <h3>Input</h3>
            {node.data.nodeType === 'trigger' ? (
              <p className="node-io-empty">
                {TRIGGER_SOURCE_HINT[String(node.data.config.trigger_kind ?? 'schedule')] ??
                  TRIGGER_SOURCE_HINT.schedule}
              </p>
            ) : upstream.length > 0 ? (
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
                    <OutputViewer data={entry.result.output} />
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
                <OutputViewer data={workflowInput} />
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

          <div className="node-io-col node-io-params">
            <h3>Parameters</h3>
            <NodeParamsFields node={node} def={def} {...paramsProps} hideNodeTest />
          </div>

          <div className="node-io-col">
            <h3>Output</h3>
            {node.data.nodeType === 'trigger' ? (
              workflowInput ? (
                <div className="node-io-block">
                  <OutputViewer data={workflowInput} />
                </div>
              ) : (
                <p className="node-io-empty">
                  No runs yet — the latest run's input (e.g. a form submission) will appear here.
                </p>
              )
            ) : testError ? (
              <p className="error-text">{testError}</p>
            ) : shownOutput ? (
              <div className="node-io-block">
                {shownOutput.error && <p className="error-text">{shownOutput.error}</p>}
                <OutputViewer data={shownOutput.output} />
              </div>
            ) : (
              <p className="node-io-empty">
                No output yet — click <strong>▶ Execute step</strong> to run just this node, or run the
                whole workflow.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
