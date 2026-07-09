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
import type { AgentPresetDef, ConnectorDef, NodeTypeDef } from '../types'
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
  onNodesChange: OnNodesChange<StudioNode>
  onEdgesChange: OnEdgesChange
  onConnect: OnConnect
  onSelectionChange: OnSelectionChangeFunc
  onDropNode: (picked: PickedNode, position: { x: number; y: number }) => void
  onAddConnected: (sourceId: string, picked: PickedNode) => void
  onAddAttached: (agentId: string, port: 'model' | 'memory' | 'tools', picked: PickedNode) => void
}

export function Canvas({
  nodes,
  edges,
  nodeDefs,
  connectors,
  agentPresets,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onSelectionChange,
  onDropNode,
  onAddConnected,
  onAddAttached,
}: CanvasProps) {
  const { screenToFlowPosition } = useReactFlow()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const [connectFrom, setConnectFrom] = useState<ConnectRequest | null>(null)

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const type = event.dataTransfer.getData('application/genxai-node-type')
      if (!type) return
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      onDropNode({ type }, position)
    },
    [screenToFlowPosition, onDropNode],
  )

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

  return (
    <div className="canvas" ref={wrapperRef} onDragOver={onDragOver} onDrop={onDrop}>
      <ConnectPickerContext.Provider value={setConnectFrom}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onSelectionChange={onSelectionChange}
        fitView
        deleteKeyCode={['Backspace', 'Delete']}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <MiniMap pannable zoomable />
        <Controls />
      </ReactFlow>
      </ConnectPickerContext.Provider>
      {nodes.length === 0 && (
        <FirstStepPicker
          nodeDefs={nodeDefs}
          connectors={connectors}
          agentPresets={agentPresets}
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
          agentPresets={connectFrom.port ? [] : agentPresets}
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
