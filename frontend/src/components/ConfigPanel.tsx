import type { Edge } from '@xyflow/react'
import { useState } from 'react'

import { apiBase } from '../api'

import type { StudioNode } from '../lib/translate'
import { OutputViewer } from './NodeIOView'
import type { UpstreamEntry } from './NodeIOView'
import { NodeParamsFields } from './NodeParamsFields'
import type { AutomationConfig, ConnectorDef, CredentialSummary, ModelOption, NodeResult, NodeTypeDef, ToolDef, WorkflowSummary, McpServerSummary, FlowPatternDef } from '../types'

interface ConfigPanelProps {
  node: StudioNode | null
  edge: Edge | null
  nodeResult?: NodeResult
  /** Upstream nodes feeding this node — shown as its input, n8n-style. */
  upstream?: UpstreamEntry[]
  /** The last run's input JSON — the input of entry nodes. */
  workflowInput?: Record<string, unknown> | null
  /** Open the full side-by-side input/output view. */
  onOpenIO?: (nodeId: string) => void
  /** Saved automation — used to show a trigger node's live URL after save. */
  automation?: AutomationConfig
  /** Save the workflow (from the trigger's "Save & create …" button). */
  onSaveWorkflow?: () => void
  nodeTypes: NodeTypeDef[]
  tools: ToolDef[]
  models: ModelOption[]
  connectors: ConnectorDef[]
  credentials: CredentialSummary[]
  mcpServers: McpServerSummary[]
  workflows: WorkflowSummary[]
  flows: FlowPatternDef[]
  currentWorkflowId: string | null
  onNodeConfigChange: (nodeId: string, config: Record<string, unknown>, label?: string) => void
  onEdgeChange: (edgeId: string, data: { condition: string | null; parallel: boolean }) => void
  onDeleteNode: (nodeId: string) => void
  onDeleteEdge: (edgeId: string) => void
}

export function ConfigPanel({
  node,
  edge,
  nodeResult,
  upstream = [],
  workflowInput = null,
  onOpenIO,
  automation,
  onSaveWorkflow,
  nodeTypes,
  tools,
  models,
  connectors,
  credentials,
  mcpServers,
  workflows,
  flows,
  currentWorkflowId,
  onNodeConfigChange,
  onEdgeChange,
  onDeleteNode,
  onDeleteEdge,
}: ConfigPanelProps) {
  if (edge) {
    const condition = (edge.data?.condition as string | null) ?? ''
    const parallel = Boolean(edge.data?.parallel)
    return (
      <aside className="config-panel">
        <h2>Edge</h2>
        <p className="config-subtitle">
          {edge.source} → {edge.target}
        </p>
        <label className="field">
          <span>Condition</span>
          <input
            value={condition}
            placeholder="e.g. input.age >= 18"
            onChange={(event) =>
              onEdgeChange(edge.id, { condition: event.target.value || null, parallel })
            }
          />
        </label>
        <p className="config-subtitle">
          Take this path when the condition passes. Supports{' '}
          <code>{'>'}</code> <code>{'<'}</code> <code>{'>='}</code> <code>{'<='}</code>{' '}
          <code>==</code> <code>!=</code> <code>contains</code> and <code>not</code>, e.g.{' '}
          <code>input.status == 'active'</code> or <code>score.output.value {'>'} 0.8</code>. Quote
          text literals; draw several edges for multi-way (switch) routing.
        </p>
        <label className="field field-checkbox">
          <input
            type="checkbox"
            checked={parallel}
            onChange={(event) =>
              onEdgeChange(edge.id, { condition: condition || null, parallel: event.target.checked })
            }
          />
          <span>Run in parallel</span>
        </label>
        <button className="danger" onClick={() => onDeleteEdge(edge.id)}>
          Delete edge
        </button>
      </aside>
    )
  }

  if (!node) {
    return (
      <aside className="config-panel">
        <h2>Inspector</h2>
        <p className="config-subtitle">Select a node or edge to configure it.</p>
      </aside>
    )
  }

  const def = nodeTypes.find((t) => t.type === node.data.nodeType)

  return (
    <aside className="config-panel">
      <div className="config-panel-titlebar">
        <div>
          <h2>{def?.label ?? node.data.nodeType}</h2>
          <p className="config-subtitle">{def?.description}</p>
        </div>
        {onOpenIO && (
          <button
            type="button"
            className="node-io-open"
            title="Open the side-by-side Input · Parameters · Output editor (or double-click the node)"
            onClick={() => onOpenIO(node.id)}
          >
            ⛶ Open editor
          </button>
        )}
      </div>

      <NodeParamsFields
        node={node}
        def={def}
        automation={automation}
        onSaveWorkflow={onSaveWorkflow}
        onOpenIO={onOpenIO}
        tools={tools}
        models={models}
        connectors={connectors}
        credentials={credentials}
        mcpServers={mcpServers}
        workflows={workflows}
        flows={flows}
        currentWorkflowId={currentWorkflowId}
        onNodeConfigChange={onNodeConfigChange}
      />

      {node.data.nodeType === 'trigger' && (
        <div className="node-output">
          <h3>
            Output{' '}
            {workflowInput && (
              <span className="node-output-status status-completed">latest run</span>
            )}
          </h3>
          {workflowInput ? (
            <OutputViewer data={workflowInput} />
          ) : (
            <p className="config-subtitle">
              No runs yet — the latest run's input (e.g. a form submission) appears here.
            </p>
          )}
        </div>
      )}

      {node.data.nodeType !== 'trigger' && (
        <div className="node-input">
          <h3>Input</h3>
          {upstream.length > 0 ? (
            upstream.map((entry) => (
              <details className="node-input-source" key={entry.id} open={upstream.length === 1}>
                <summary>
                  <code>{entry.id}</code>
                  {entry.result && (
                    <span className={`node-output-status status-${entry.result.status}`}>
                      {entry.result.status}
                    </span>
                  )}
                </summary>
                {entry.result ? (
                  <OutputViewer data={entry.result.output} />
                ) : (
                  <p className="config-subtitle">No data yet — run the workflow.</p>
                )}
              </details>
            ))
          ) : workflowInput ? (
            <details className="node-input-source" open>
              <summary>
                <code>input</code> — workflow input
              </summary>
              <OutputViewer data={workflowInput} />
            </details>
          ) : (
            <p className="config-subtitle">Entry node — receives the workflow's run input.</p>
          )}
        </div>
      )}

      {nodeResult && (
        <div className="node-output">
          <h3>
            Output{' '}
            <span className={`node-output-status status-${nodeResult.status}`}>
              {nodeResult.status}
            </span>
            {nodeResult.duration_ms != null && (
              <span className="node-output-duration">{nodeResult.duration_ms}ms</span>
            )}
          </h3>
          {nodeResult.error && <p className="error-text">{nodeResult.error}</p>}
          <OutputViewer data={nodeResult.output} />
          <FileRefLinks output={nodeResult.output} />
          <OutputPathPicker nodeId={node.id} output={nodeResult.output} />
        </div>
      )}

      <button className="danger" onClick={() => onDeleteNode(node.id)}>
        Delete node
      </button>
    </aside>
  )
}

function flattenPaths(value: unknown, prefix: string, depth: number, out: string[]): void {
  if (out.length >= 40 || depth > 4 || value === null || typeof value !== 'object') {
    if (prefix) out.push(prefix)
    return
  }
  if (Array.isArray(value)) {
    if (value.length > 0) flattenPaths(value[0], `${prefix}.0`, depth + 1, out)
    return
  }
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (out.length >= 40) return
    flattenPaths(item, prefix ? `${prefix}.${key}` : key, depth + 1, out)
  }
}

interface FileRef {
  id: string
  name: string
  media_type?: string
  size?: number
}

function collectFileRefs(value: unknown, depth: number, out: FileRef[]): void {
  if (depth > 5 || value === null || typeof value !== 'object' || out.length >= 10) return
  const record = value as Record<string, unknown>
  if (record.__genxai_file__ === true && typeof record.id === 'string') {
    out.push({
      id: record.id,
      name: String(record.name ?? record.id),
      media_type: record.media_type as string | undefined,
      size: record.size as number | undefined,
    })
    return
  }
  for (const item of Array.isArray(value) ? value : Object.values(record)) {
    collectFileRefs(item, depth + 1, out)
  }
}

function FileRefLinks({ output }: { output: unknown }) {
  const refs: FileRef[] = []
  collectFileRefs(output, 0, refs)
  if (refs.length === 0) return null
  return (
    <div className="file-ref-links">
      {refs.map((ref) => (
        <a
          key={ref.id}
          href={`${apiBase}/files/${ref.id}`}
          target="_blank"
          rel="noreferrer"
          title={ref.media_type}
        >
          📎 {ref.name}
          {ref.size != null && ` (${(ref.size / 1024).toFixed(1)} KB)`}
        </a>
      ))}
    </div>
  )
}

function OutputPathPicker({ nodeId, output }: { nodeId: string; output: unknown }) {
  const [copied, setCopied] = useState<string | null>(null)
  const paths: string[] = []
  flattenPaths(output, '', 0, paths)
  if (paths.length === 0) return null

  return (
    <details className="output-paths">
      <summary>Reference these values downstream</summary>
      {paths.map((path) => {
        const expression = `{{ ${nodeId}.${path} }}`
        return (
          <button
            key={path}
            className="output-path"
            title={`Copy ${expression}`}
            onClick={() => {
              navigator.clipboard.writeText(expression)
              setCopied(path)
              setTimeout(() => setCopied(null), 1200)
            }}
          >
            {copied === path ? '✓ copied' : expression}
          </button>
        )
      })}
    </details>
  )
}
