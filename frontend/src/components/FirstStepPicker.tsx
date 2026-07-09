import { useState } from 'react'

import type { AgentPresetDef, ConnectorDef, McpServerSummary, NodeTypeDef, ToolDef } from '../types'
import { NodePicker, type PickedNode } from './NodePicker'

interface FirstStepPickerProps {
  nodeDefs: NodeTypeDef[]
  connectors?: ConnectorDef[]
  agentPresets?: AgentPresetDef[]
  tools?: ToolDef[]
  mcpServers?: McpServerSummary[]
  onPick: (picked: PickedNode) => void
}

/** n8n-style empty-canvas affordance: a dashed + box that opens a
 * searchable node picker; picking a type drops it in the canvas center. */
export function FirstStepPicker({
  nodeDefs,
  connectors,
  agentPresets,
  tools,
  mcpServers,
  onPick,
}: FirstStepPickerProps) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <div className="first-step-overlay">
        <button
          type="button"
          className="first-step-box"
          onClick={() => setOpen(true)}
          aria-label="Add first step"
        >
          <span className="first-step-plus">+</span>
        </button>
        <div className="first-step-hint">Add first step…</div>
      </div>
      {open && (
        <NodePicker
          nodeDefs={nodeDefs}
          connectors={connectors}
          agentPresets={agentPresets}
          tools={tools}
          mcpServers={mcpServers}
          onClose={() => setOpen(false)}
          onPick={(picked) => {
            onPick(picked)
            setOpen(false)
          }}
        />
      )}
    </>
  )
}
