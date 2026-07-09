import { useState } from 'react'

import { nodeIcon } from '../lib/nodeIcons'
import type { NodeTypeDef } from '../types'

interface NodePickerProps {
  nodeDefs: NodeTypeDef[]
  onPick: (type: string) => void
  onClose: () => void
}

/** Searchable right-side node picker panel (n8n-style). */
export function NodePicker({ nodeDefs, onPick, onClose }: NodePickerProps) {
  const [query, setQuery] = useState('')

  const needle = query.trim().toLowerCase()
  const filtered = needle
    ? nodeDefs.filter((def) =>
        `${def.label} ${def.type} ${def.description}`.toLowerCase().includes(needle),
      )
    : nodeDefs

  return (
    <>
      <div className="node-picker-backdrop" onClick={onClose} />
      <div className="node-picker">
        <input
          autoFocus
          placeholder="Search nodes…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') onClose()
            if (event.key === 'Enter' && filtered.length > 0) onPick(filtered[0].type)
          }}
        />
        <ul>
          {filtered.map((def) => (
            <li key={def.type} onClick={() => onPick(def.type)}>
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
  )
}
