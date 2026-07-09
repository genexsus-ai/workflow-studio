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
import { useCallback, useRef } from 'react'

import type { StudioNode } from '../lib/translate'
import type { NodeTypeDef } from '../types'
import { FirstStepPicker } from './FirstStepPicker'
import { StudioNode as StudioNodeComponent } from './nodes/StudioNode'

const nodeTypes: NodeTypes = { studio: StudioNodeComponent }

interface CanvasProps {
  nodes: StudioNode[]
  edges: Edge[]
  nodeDefs: NodeTypeDef[]
  onNodesChange: OnNodesChange<StudioNode>
  onEdgesChange: OnEdgesChange
  onConnect: OnConnect
  onSelectionChange: OnSelectionChangeFunc
  onDropNode: (type: string, position: { x: number; y: number }) => void
}

export function Canvas({
  nodes,
  edges,
  nodeDefs,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onSelectionChange,
  onDropNode,
}: CanvasProps) {
  const { screenToFlowPosition } = useReactFlow()
  const wrapperRef = useRef<HTMLDivElement>(null)

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

  const addAtCenter = useCallback(
    (type: string) => {
      const rect = wrapperRef.current?.getBoundingClientRect()
      const position = rect
        ? screenToFlowPosition({
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
          })
        : { x: 250, y: 200 }
      onDropNode(type, position)
    },
    [screenToFlowPosition, onDropNode],
  )

  return (
    <div className="canvas" ref={wrapperRef} onDragOver={onDragOver} onDrop={onDrop}>
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
      {nodes.length === 0 && <FirstStepPicker nodeDefs={nodeDefs} onPick={addAtCenter} />}
    </div>
  )
}
