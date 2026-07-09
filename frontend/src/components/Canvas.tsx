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

import { ConnectPickerContext } from '../lib/connectContext'
import type { StudioNode } from '../lib/translate'
import type { NodeTypeDef } from '../types'
import { FirstStepPicker } from './FirstStepPicker'
import { NodePicker } from './NodePicker'
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
  onAddConnected: (sourceId: string, type: string) => void
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
  onAddConnected,
}: CanvasProps) {
  const { screenToFlowPosition } = useReactFlow()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const [connectFrom, setConnectFrom] = useState<string | null>(null)

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
      {nodes.length === 0 && <FirstStepPicker nodeDefs={nodeDefs} onPick={addAtCenter} />}
      {connectFrom && (
        <NodePicker
          nodeDefs={nodeDefs.filter((def) => !['trigger', 'input'].includes(def.type))}
          onClose={() => setConnectFrom(null)}
          onPick={(type) => {
            onAddConnected(connectFrom, type)
            setConnectFrom(null)
          }}
        />
      )}
    </div>
  )
}
