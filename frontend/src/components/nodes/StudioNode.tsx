import { Handle, Position, useReactFlow, useStore, type NodeProps } from '@xyflow/react'

import type { StudioNode as StudioNodeType } from '../../lib/translate'
import { useConnectPicker } from '../../lib/connectContext'
import { nodeIcon } from '../../lib/nodeIcons'

const STATUS_COLORS: Record<string, string> = {
  idle: 'transparent',
  running: '#3b82f6',
  completed: '#22c55e',
  failed: '#ef4444',
  skipped: '#eab308',
}

// Capability nodes hang off an agent's bottom ports instead of joining the flow
const CAPABILITY_TYPES = new Set(['model', 'memory'])
// Node types that can also be attached to an agent's Tools port
const ATTACHABLE_TOOL_TYPES = new Set(['tool', 'mcp'])
// Flow sources: nothing can connect INTO these (n8n-style single output)
const NO_TARGET_TYPES = new Set(['input', 'trigger'])

const AGENT_PORTS = [
  { id: 'attach_model', label: 'Model', left: '22%' },
  { id: 'attach_memory', label: 'Memory', left: '50%' },
  { id: 'attach_tools', label: 'Tools', left: '78%' },
]

export function StudioNode({ id, data, selected }: NodeProps<StudioNodeType>) {
  const { deleteElements } = useReactFlow()
  const openConnectPicker = useConnectPicker()
  const hasOutgoing = useStore((state) =>
    state.edges.some((edge) => edge.source === id && !edge.data?.attach),
  )
  const ring = STATUS_COLORS[data.status] ?? 'transparent'
  const isCapability = CAPABILITY_TYPES.has(data.nodeType)
  const isTrigger = data.nodeType === 'trigger'
  const subtitle =
    data.nodeType === 'agent'
      ? (data.config.role as string | undefined)
      : data.nodeType === 'tool'
        ? (data.config.tool_name as string | undefined)
        : data.nodeType === 'connector'
          ? [data.config.connector, data.config.action].filter(Boolean).join(' · ') || undefined
          : data.nodeType === 'trigger'
            ? data.config.trigger_kind === 'schedule'
              ? `every ${String(data.config.interval_seconds ?? 3600)}s`
              : 'webhook'
            : data.nodeType === 'model'
              ? (data.config.llm_model as string | undefined)
              : data.nodeType === 'memory'
                ? (data.config.persistent === false ? 'this run only' : 'persistent')
                : data.nodeType === 'flow'
                  ? `${String(data.config.flow_type ?? 'pattern')} · ${Array.isArray(data.config.agents) ? data.config.agents.length : 0} agents`
                  : undefined

  const classes = [
    'studio-node',
    isCapability && 'studio-node-capability',
    isTrigger && 'studio-node-trigger',
    selected && 'selected',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={classes}
      style={{
        borderColor: selected ? data.color : undefined,
        boxShadow: ring === 'transparent' ? undefined : `0 0 0 3px ${ring}55, 0 1px 3px rgba(15, 23, 42, 0.1)`,
      }}
    >
      <button
        type="button"
        className="studio-node-delete nodrag"
        title="Delete node"
        aria-label="Delete node"
        onClick={(event) => {
          event.stopPropagation()
          void deleteElements({ nodes: [{ id }] })
        }}
      >
        ✕
      </button>
      {isTrigger && <span className="studio-node-bolt">⚡</span>}
      {!isCapability && !NO_TARGET_TYPES.has(data.nodeType) && (
        <Handle type="target" position={Position.Left} />
      )}
      <div className="studio-node-body">
        <span
          className="studio-node-icon"
          style={{ background: `${data.color}1c`, borderColor: `${data.color}44` }}
        >
          {nodeIcon(data.nodeType, data.config)}
        </span>
        <div className="studio-node-text">
          <div className="studio-node-label">{data.label}</div>
          <div className="studio-node-subtitle">{subtitle ?? data.nodeType}</div>
        </div>
        {data.status === 'running' && <span className="studio-node-spinner" />}
      </div>
      {!isCapability && data.nodeType !== 'output' && (
        <Handle type="source" position={Position.Right} />
      )}
      {!isCapability && data.nodeType !== 'output' && !hasOutgoing && (
        <button
          type="button"
          className="studio-node-add nodrag"
          title="Add connected node"
          aria-label="Add connected node"
          onClick={(event) => {
            event.stopPropagation()
            openConnectPicker(id)
          }}
        >
          +
        </button>
      )}
      {(isCapability || ATTACHABLE_TOOL_TYPES.has(data.nodeType)) && (
        <Handle
          type="source"
          id="attach"
          position={Position.Top}
          className="studio-attach-handle"
          title="Attach to an agent port"
        />
      )}
      {data.nodeType === 'agent' && (
        <div className="studio-node-ports">
          {AGENT_PORTS.map((port) => (
            <div key={port.id}>
              <Handle
                type="target"
                id={port.id}
                position={Position.Bottom}
                className="studio-attach-handle"
                style={{ left: port.left }}
              />
              <span className="studio-port-label" style={{ left: port.left }}>
                {port.label}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
