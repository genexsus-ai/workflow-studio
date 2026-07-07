import type { NodeTypeDef } from '../types'

interface PaletteProps {
  nodeTypes: NodeTypeDef[]
}

export function Palette({ nodeTypes }: PaletteProps) {
  const onDragStart = (event: React.DragEvent, type: string) => {
    event.dataTransfer.setData('application/genxai-node-type', type)
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <aside className="palette">
      <h2>Components</h2>
      <p className="palette-hint">Drag onto the canvas</p>
      {nodeTypes.map((def) => (
        <div
          key={def.type}
          className="palette-item"
          draggable
          onDragStart={(event) => onDragStart(event, def.type)}
          title={def.description}
        >
          <span className="palette-dot" style={{ background: def.color }} />
          <div>
            <div className="palette-label">{def.label}</div>
            <div className="palette-description">{def.description}</div>
          </div>
        </div>
      ))}
    </aside>
  )
}
