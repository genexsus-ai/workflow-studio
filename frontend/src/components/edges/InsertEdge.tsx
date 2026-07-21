import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'

import { useInsertPicker } from '../../lib/connectContext'

/**
 * A normal flow edge with a "+" button at its midpoint — click it to insert
 * a node in the middle of the connection (Zapier / n8n style).
 */
export function InsertEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
}: EdgeProps) {
  const openInsertPicker = useInsertPicker()
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <button
          type="button"
          className="edge-insert-button nodrag nopan"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          title="Insert a node here"
          aria-label="Insert a node here"
          onClick={(event) => {
            event.stopPropagation()
            openInsertPicker(id)
          }}
        >
          +
        </button>
      </EdgeLabelRenderer>
    </>
  )
}
