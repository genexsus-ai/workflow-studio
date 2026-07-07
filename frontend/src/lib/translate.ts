import type { Edge, Node } from '@xyflow/react'

import type { EdgeDoc, NodeDoc, NodeRunStatus, WorkflowDoc } from '../types'

export interface StudioNodeData extends Record<string, unknown> {
  nodeType: string
  label: string
  config: Record<string, unknown>
  status: NodeRunStatus
  color: string
}

export type StudioNode = Node<StudioNodeData>

const FALLBACK_COLOR = '#64748b'

// Capability edges (model/memory/tools hanging off an agent) render dashed,
// like n8n's sub-node connections.
export const ATTACH_EDGE_STYLE = { strokeDasharray: '6 4' } as const

export function docToFlow(
  doc: WorkflowDoc,
  colorFor: (type: string) => string | undefined,
): { nodes: StudioNode[]; edges: Edge[] } {
  const nodes: StudioNode[] = doc.nodes.map((node) => ({
    id: node.id,
    type: 'studio',
    position: node.position ?? { x: 0, y: 0 },
    data: {
      nodeType: node.type,
      label: node.label ?? node.id,
      config: node.config ?? {},
      status: 'idle',
      color: colorFor(node.type) ?? FALLBACK_COLOR,
    },
  }))
  const edges: Edge[] = doc.edges.map((edge, index) =>
    edge.attach
      ? {
          id: edge.id ?? `e${index}-${edge.source}-${edge.target}`,
          source: edge.source,
          sourceHandle: 'attach',
          target: edge.target,
          targetHandle: `attach_${edge.attach}`,
          style: ATTACH_EDGE_STYLE,
          data: { condition: null, parallel: false, attach: edge.attach },
        }
      : {
          id: edge.id ?? `e${index}-${edge.source}-${edge.target}`,
          source: edge.source,
          target: edge.target,
          animated: Boolean(edge.parallel),
          label: edge.condition ?? undefined,
          data: { condition: edge.condition ?? null, parallel: Boolean(edge.parallel) },
        },
  )
  return { nodes, edges }
}

export function flowToDoc(
  name: string,
  description: string,
  nodes: StudioNode[],
  edges: Edge[],
  id?: string | null,
): WorkflowDoc {
  const nodeDocs: NodeDoc[] = nodes.map((node) => ({
    id: node.id,
    type: node.data.nodeType,
    label: node.data.label,
    position: { x: node.position.x, y: node.position.y },
    config: node.data.config,
  }))
  const edgeDocs: EdgeDoc[] = edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    condition: (edge.data?.condition as string | null) ?? null,
    parallel: Boolean(edge.data?.parallel),
    attach: (edge.data?.attach as string | null) ?? null,
  }))
  return { id: id ?? null, name, description, nodes: nodeDocs, edges: edgeDocs }
}

let counter = 0

export function nextNodeId(type: string, taken: Set<string>): string {
  for (;;) {
    counter += 1
    const candidate = `${type}_${counter}`
    if (!taken.has(candidate)) return candidate
  }
}
