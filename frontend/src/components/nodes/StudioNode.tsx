import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'

import type { StudioNode as StudioNodeType } from '../../lib/translate'

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

const AGENT_PORTS = [
  { id: 'attach_model', label: 'Model', left: '22%' },
  { id: 'attach_memory', label: 'Memory', left: '50%' },
  { id: 'attach_tools', label: 'Tools', left: '78%' },
]

export function StudioNode({ id, data, selected }: NodeProps<StudioNodeType>) {
  const { deleteElements } = useReactFlow()
  const ring = STATUS_COLORS[data.status] ?? 'transparent'
  const isCapability = CAPABILITY_TYPES.has(data.nodeType)
  const subtitle =
    data.nodeType === 'agent'
      ? (data.config.role as string | undefined)
      : data.nodeType === 'tool'
        ? (data.config.tool_name as string | undefined)
        : data.nodeType === 'model'
          ? (data.config.llm_model as string | undefined)
          : data.nodeType === 'memory'
            ? (data.config.persistent === false ? 'this run only' : 'persistent')
            : data.nodeType === 'flow'
              ? `${String(data.config.flow_type ?? 'pattern')} · ${Array.isArray(data.config.agents) ? data.config.agents.length : 0} agents`
              : undefined

  return (
    <div
      className={`${isCapability ? 'studio-node studio-node-capability' : 'studio-node'}${selected ? ' selected' : ''}`}
      style={{
        borderColor: selected ? data.color : '#d4d4d8',
        boxShadow: ring === 'transparent' ? undefined : `0 0 0 3px ${ring}`,
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
      {!isCapability && data.nodeType !== 'input' && (
        <Handle type="target" position={Position.Left} />
      )}
      <div className="studio-node-header">
        <span className="studio-node-dot" style={{ background: data.color }} />
        <span className="studio-node-type">{data.nodeType}</span>
        {data.status === 'running' && <span className="studio-node-spinner" />}
      </div>
      <div className="studio-node-label">{data.label}</div>
      {subtitle && <div className="studio-node-subtitle">{subtitle}</div>}
      {!isCapability && data.nodeType !== 'output' && (
        <Handle type="source" position={Position.Right} />
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
