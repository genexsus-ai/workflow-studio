import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Edge,
  type NodeTypes,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  type OnSelectionChangeFunc,
} from '@xyflow/react'
import { useCallback, useRef, useState } from 'react'

import { ConnectPickerContext, PORT_TYPES, type ConnectRequest } from '../lib/connectContext'
import type { StudioNode } from '../lib/translate'
import type { AgentPresetDef, ConnectorDef, FlowPatternDef, McpServerSummary, NodeTypeDef, ToolDef } from '../types'
import { FirstStepPicker } from './FirstStepPicker'
import { NodePicker, type PickedNode } from './NodePicker'
import { StudioNode as StudioNodeComponent } from './nodes/StudioNode'

const nodeTypes: NodeTypes = { studio: StudioNodeComponent }

interface CanvasProps {
  nodes: StudioNode[]
  edges: Edge[]
  nodeDefs: NodeTypeDef[]
  connectors: ConnectorDef[]
  agentPresets: AgentPresetDef[]
  tools: ToolDef[]
  mcpServers: McpServerSummary[]
  flows: FlowPatternDef[]
  onNodesChange: OnNodesChange<StudioNode>
  onEdgesChange: OnEdgesChange
  onConnect: OnConnect
  onSelectionChange: OnSelectionChangeFunc
  /** Open the n8n-style input/output detail view for a node (double-click). */
  onNodeOpenIO: (nodeId: string) => void
  onDropNode: (picked: PickedNode, position: { x: number; y: number }) => void
  onAddConnected: (sourceId: string, picked: PickedNode) => void
  onAddAttached: (agentId: string, port: 'model' | 'memory' | 'tools' | 'agents', picked: PickedNode) => void
  onDeleteNode: (nodeId: string) => void
  onNodeConfigChange: (nodeId: string, config: Record<string, unknown>, label?: string) => void
}

// The trigger is a workflow-level automation declaration, not a flow step,
// so it renders as a fixed corner badge instead of a draggable node.
function triggerSubtitle(config: Record<string, unknown>): string {
  if (config.trigger_kind === 'form') return 'form'
  if (config.trigger_kind !== 'schedule') return 'webhook'
  if (config.cron) {
    const tz = config.timezone && config.timezone !== 'UTC' ? ` · ${String(config.timezone)}` : ''
    return `cron ${String(config.cron)}${tz}`
  }
  return `every ${String(config.interval_seconds ?? 3600)}s`
}

export function Canvas({
  nodes,
  edges,
  nodeDefs,
  connectors,
  agentPresets,
  tools,
  mcpServers,
  flows,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onSelectionChange,
  onNodeOpenIO,
  onDropNode,
  onAddConnected,
  onAddAttached,
  onDeleteNode,
  onNodeConfigChange,
}: CanvasProps) {
  const { screenToFlowPosition } = useReactFlow()
  const wrapperRef = useRef<HTMLDivElement>(null)
  // The trigger renders as a pinned badge, not a flow node, so keep it out
  // of what React Flow manages and draw it as a fixed overlay instead.
  const triggerNode = nodes.find((node) => node.data?.nodeType === 'trigger')
  const flowNodes = triggerNode
    ? nodes.filter((node) => node.data?.nodeType !== 'trigger')
    : nodes
  const [connectFrom, setConnectFrom] = useState<ConnectRequest | null>(null)
  const [freePickOpen, setFreePickOpen] = useState(false)
  const [dark, setDark] = useState(
    () => localStorage.getItem('genxai-canvas-dark') === '1',
  )

  const toggleDark = useCallback(() => {
    setDark((value) => {
      localStorage.setItem('genxai-canvas-dark', value ? '0' : '1')
      return !value
    })
  }, [])

  const addAtCenter = useCallback(
    (picked: PickedNode) => {
      const rect = wrapperRef.current?.getBoundingClientRect()
      const position = rect
        ? screenToFlowPosition({
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
          })
        : { x: 250, y: 200 }
      onDropNode(picked, position)
    },
    [screenToFlowPosition, onDropNode],
  )

  // The trigger renders as a fixed corner badge, so its stored position is
  // irrelevant — drop it at the origin.
  const addTrigger = useCallback(() => {
    onDropNode({ type: 'trigger' }, { x: 0, y: 0 })
  }, [onDropNode])

  return (
    <div className={`canvas${dark ? ' dark' : ''}`} ref={wrapperRef}>
      <ConnectPickerContext.Provider value={setConnectFrom}>
      <ReactFlow
        colorMode={dark ? 'dark' : 'light'}
        nodes={flowNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onSelectionChange={onSelectionChange}
        onNodeDoubleClick={(_, node) => onNodeOpenIO(node.id)}
        fitView
        deleteKeyCode={['Backspace', 'Delete']}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
      </ConnectPickerContext.Provider>
      {nodes.length > 0 && (
        <button
          type="button"
          className="canvas-add-node"
          title="Add node"
          aria-label="Add node"
          onClick={() => setFreePickOpen(true)}
        >
          ＋
        </button>
      )}
      {/* A workflow uses at most one trigger; show the add button only while
          none exists, and the pinned badge once one does. */}
      {nodes.length > 0 && !triggerNode && (
        <button
          type="button"
          className="canvas-add-trigger"
          title="Add a schedule or webhook trigger"
          aria-label="Add trigger"
          onClick={addTrigger}
        >
          ⚡
        </button>
      )}
      {triggerNode &&
        (() => {
          const triggerOn = triggerNode.data.config.enabled !== false
          return (
            <div
              className={`canvas-trigger-badge${triggerOn ? ' on' : ' off'}`}
              role="button"
              tabIndex={0}
              title="Edit trigger"
              onClick={() => onSelectionChange({ nodes: [triggerNode], edges: [] })}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelectionChange({ nodes: [triggerNode], edges: [] })
                }
              }}
            >
              <button
                type="button"
                className="canvas-trigger-badge-toggle"
                title={triggerOn ? 'Trigger is on — click to turn off' : 'Trigger is off — click to turn on'}
                aria-label={triggerOn ? 'Turn trigger off' : 'Turn trigger on'}
                aria-pressed={triggerOn}
                onClick={(event) => {
                  event.stopPropagation()
                  onNodeConfigChange(triggerNode.id, {
                    ...triggerNode.data.config,
                    enabled: !triggerOn,
                  })
                }}
              >
                <span className="canvas-trigger-badge-dot" />
                {triggerOn ? 'On' : 'Off'}
              </button>
              <span className="canvas-trigger-badge-text">
                <span className="canvas-trigger-badge-label">{triggerNode.data.label}</span>
                <span className="canvas-trigger-badge-sub">{triggerSubtitle(triggerNode.data.config)}</span>
              </span>
              <button
                type="button"
                className="canvas-trigger-badge-remove"
                title="Remove trigger"
                aria-label="Remove trigger"
                onClick={(event) => {
                  event.stopPropagation()
                  onDeleteNode(triggerNode.id)
                }}
              >
                ✕
              </button>
            </div>
          )
        })()}
      <button
        type="button"
        className="canvas-theme-toggle"
        title={dark ? 'Switch canvas to light' : 'Switch canvas to dark'}
        aria-label="Toggle canvas theme"
        onClick={toggleDark}
      >
        {dark ? '☀️' : '🌙'}
      </button>
      {freePickOpen && (
        <NodePicker
          nodeDefs={
            triggerNode ? nodeDefs.filter((def) => def.type !== 'trigger') : nodeDefs
          }
          connectors={connectors}
          agentPresets={agentPresets}
          tools={tools}
          mcpServers={mcpServers}
          flows={flows}
          onClose={() => setFreePickOpen(false)}
          onPick={(picked) => {
            addAtCenter(picked)
            setFreePickOpen(false)
          }}
        />
      )}
      {nodes.length === 0 && (
        <FirstStepPicker
          nodeDefs={nodeDefs}
          connectors={connectors}
          agentPresets={agentPresets}
          tools={tools}
          mcpServers={mcpServers}
          flows={flows}
          onPick={addAtCenter}
        />
      )}
      {connectFrom && (
        <NodePicker
          nodeDefs={
            connectFrom.port
              ? nodeDefs.filter((def) => PORT_TYPES[connectFrom.port!].includes(def.type))
              : nodeDefs.filter((def) => !['trigger', 'input'].includes(def.type))
          }
          connectors={connectFrom.port ? [] : connectors}
          agentPresets={connectFrom.port && connectFrom.port !== 'agents' ? [] : agentPresets}
          tools={connectFrom.port && connectFrom.port !== 'tools' ? [] : tools}
          mcpServers={connectFrom.port && connectFrom.port !== 'tools' ? [] : mcpServers}
          flows={connectFrom.port ? [] : flows}
          onClose={() => setConnectFrom(null)}
          onPick={(picked) => {
            if (connectFrom.port) {
              onAddAttached(connectFrom.nodeId, connectFrom.port, picked)
            } else {
              onAddConnected(connectFrom.nodeId, picked)
            }
            setConnectFrom(null)
          }}
        />
      )}
    </div>
  )
}
