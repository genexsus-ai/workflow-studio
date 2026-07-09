import { useState } from 'react'

import type { NodeTypeDef } from '../types'
import { NodePicker } from './NodePicker'

interface FirstStepPickerProps {
  nodeDefs: NodeTypeDef[]
  onPick: (type: string) => void
}

/** n8n-style empty-canvas affordance: a dashed + box that opens a
 * searchable node picker; picking a type drops it in the canvas center. */
export function FirstStepPicker({ nodeDefs, onPick }: FirstStepPickerProps) {
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
          onClose={() => setOpen(false)}
          onPick={(type) => {
            onPick(type)
            setOpen(false)
          }}
        />
      )}
    </>
  )
}
