import { useState } from 'react'

import { nodeIcon } from '../lib/nodeIcons'
import type { NodeTypeDef } from '../types'

interface FirstStepPickerProps {
  nodeDefs: NodeTypeDef[]
  onPick: (type: string) => void
}

/** n8n-style empty-canvas affordance: a dashed + box that opens a
 * searchable node picker; picking a type drops it in the canvas center. */
export function FirstStepPicker({ nodeDefs, onPick }: FirstStepPickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')

  const needle = query.trim().toLowerCase()
  const filtered = needle
    ? nodeDefs.filter((def) =>
        `${def.label} ${def.type} ${def.description}`.toLowerCase().includes(needle),
      )
    : nodeDefs

  const pick = (type: string) => {
    onPick(type)
    setOpen(false)
    setQuery('')
  }

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
        <>
          <div className="node-picker-backdrop" onClick={() => setOpen(false)} />
          <div className="node-picker">
            <input
              autoFocus
              placeholder="Search nodes…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setOpen(false)
                if (event.key === 'Enter' && filtered.length > 0) pick(filtered[0].type)
              }}
            />
            <ul>
              {filtered.map((def) => (
                <li key={def.type} onClick={() => pick(def.type)}>
                  <span className="palette-icon" style={{ background: `${def.color}1c` }}>
                    {nodeIcon(def.type)}
                  </span>
                  <div>
                    <div className="node-picker-label">{def.label}</div>
                    <div className="node-picker-description">{def.description}</div>
                  </div>
                </li>
              ))}
              {filtered.length === 0 && <li className="node-picker-empty">No matching nodes</li>}
            </ul>
          </div>
        </>
      )}
    </>
  )
}
