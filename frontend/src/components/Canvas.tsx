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
}: CanvasProps) {
  const { screenToFlowPosition } = useReactFlow()
  const wrapperRef = useRef<HTMLDivElement>(null)
  // n8n-style: the trigger is a regular canvas node at the start of the flow.
  const triggerNode = nodes.find((node) => node.data?.nodeType === 'trigger')
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

  // Drop the trigger to the left of the flow so it reads as the entry point.
  const addTrigger = useCallback(() => {
    const xs = nodes.map((node) => node.position?.x ?? 0)
    const ys = nodes.map((node) => node.position?.y ?? 0)
    const x = xs.length ? Math.min(...xs) - 240 : 80
    const y = ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 200
    onDropNode({ type: 'trigger' }, { x, y })
  }, [onDropNode, nodes])

  return (
    <div className={`canvas${dark ? ' dark' : ''}`} ref={wrapperRef}>
      <ConnectPickerContext.Provider value={setConnectFrom}>
      <ReactFlow
        colorMode={dark ? 'dark' : 'light'}
        nodes={nodes}
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
          none exists. */}
      {nodes.length > 0 && !triggerNode && (
        <button
          type="button"
          className="canvas-add-trigger"
          title="Add a trigger (schedule, webhook, or form)"
          aria-label="Add trigger"
          onClick={addTrigger}
        >
          ⚡
        </button>
      )}
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
