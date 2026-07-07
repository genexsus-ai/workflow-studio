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
import { useCallback } from 'react'

import type { StudioNode } from '../lib/translate'
import { StudioNode as StudioNodeComponent } from './nodes/StudioNode'

const nodeTypes: NodeTypes = { studio: StudioNodeComponent }

interface CanvasProps {
  nodes: StudioNode[]
  edges: Edge[]
  onNodesChange: OnNodesChange<StudioNode>
  onEdgesChange: OnEdgesChange
  onConnect: OnConnect
  onSelectionChange: OnSelectionChangeFunc
  onDropNode: (type: string, position: { x: number; y: number }) => void
}

export function Canvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onSelectionChange,
  onDropNode,
}: CanvasProps) {
  const { screenToFlowPosition } = useReactFlow()

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
      onDropNode(type, position)
    },
    [screenToFlowPosition, onDropNode],
  )

  return (
    <div className="canvas" onDragOver={onDragOver} onDrop={onDrop}>
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
    </div>
  )
}
