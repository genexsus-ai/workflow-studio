import { useMemo, useState } from 'react'

import { nodeIcon } from '../lib/nodeIcons'
import { loadRecents, saveRecent } from '../lib/recentPicks'
import type { ConnectorDef, NodeTypeDef } from '../types'

/** What a picker selection resolves to: a node type plus optional
 * pre-filled config (e.g. a connector app + action). */
export interface PickedNode {
  type: string
  config?: Record<string, unknown>
  label?: string
}

interface PickerEntry {
  key: string
  icon: string
  iconBg: string
  label: string
  description: string
  drillInto?: ConnectorDef | 'apps'
  picked?: PickedNode
}

interface NodePickerProps {
  nodeDefs: NodeTypeDef[]
  connectors?: ConnectorDef[]
  onPick: (picked: PickedNode) => void
  onClose: () => void
}

const TRIGGER_TYPES = new Set(['trigger'])

function defEntry(def: NodeTypeDef): PickerEntry {
  return {
    key: `type:${def.type}`,
    icon: nodeIcon(def.type),
    iconBg: `${def.color}1c`,
    label: def.label,
    description: def.description,
    picked: { type: def.type },
  }
}

function appEntry(app: ConnectorDef): PickerEntry {
  const actionCount = Object.keys(app.actions).length
  return {
    key: `app:${app.type}`,
    icon: app.icon ?? '🔌',
    iconBg: `${app.color}1c`,
    label: app.label,
    description: `${actionCount} action${actionCount === 1 ? '' : 's'}`,
    drillInto: app,
  }
}

function actionEntry(app: ConnectorDef, action: string, withAppName: boolean): PickerEntry {
  const spec = app.actions[action]
  return {
    key: `action:${app.type}:${action}`,
    icon: app.icon ?? '🔌',
    iconBg: `${app.color}1c`,
    label: withAppName ? `${app.label} — ${action.replaceAll('_', ' ')}` : action.replaceAll('_', ' '),
    description: spec.description,
    picked: {
      type: 'connector',
      config: { connector: app.type, action, credential: '', params: {} },
      label: app.label,
    },
  }
}

/** Searchable right-side node picker panel (n8n-style): triggers, core
 * nodes, and connector apps with per-action search and drill-in. */
export function NodePicker({ nodeDefs, connectors = [], onPick, onClose }: NodePickerProps) {
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'root' | 'apps' | ConnectorDef>('root')

  const pick = (picked: PickedNode, key: string) => {
    saveRecent(key)
    onPick(picked)
  }

  const sections = useMemo((): [string, PickerEntry[]][] => {
    const hasApps = connectors.length > 0
    // With apps available, the Connector core entry becomes a drill-in to
    // the app list (Connector -> app -> action) to keep the root compact.
    const coreDefs = nodeDefs.filter((def) => !TRIGGER_TYPES.has(def.type))
    const triggerDefs = nodeDefs.filter((def) => TRIGGER_TYPES.has(def.type))

    if (view === 'apps') {
      return [['Apps', connectors.map(appEntry)]]
    }
    if (view !== 'root') {
      return [[view.label, Object.keys(view.actions).map((a) => actionEntry(view, a, false))]]
    }

    const needle = query.trim().toLowerCase()
    if (needle) {
      const matchingDefs = [...triggerDefs, ...coreDefs]
        .filter((def) => `${def.label} ${def.type} ${def.description}`.toLowerCase().includes(needle))
        .map(defEntry)
      const matchingActions = connectors.flatMap((app) => {
        const appMatch = `${app.label} ${app.type}`.toLowerCase().includes(needle)
        return Object.keys(app.actions)
          .filter(
            (action) =>
              appMatch ||
              `${action} ${app.actions[action].description}`.toLowerCase().includes(needle),
          )
          .map((action) => actionEntry(app, action, true))
      })
      return [['Results', [...matchingDefs, ...matchingActions]]]
    }

    const byKey = new Map<string, PickerEntry>()
    for (const def of [...triggerDefs, ...coreDefs]) byKey.set(`type:${def.type}`, defEntry(def))
    for (const app of connectors) {
      byKey.set(`app:${app.type}`, appEntry(app))
      for (const action of Object.keys(app.actions)) {
        byKey.set(`action:${app.type}:${action}`, actionEntry(app, action, true))
      }
    }
    const recents = loadRecents()
      .map((key) => byKey.get(key))
      .filter((entry): entry is PickerEntry => Boolean(entry))
      .slice(0, 4)

    const coreEntries = coreDefs.map((def) =>
      hasApps && def.type === 'connector'
        ? {
            ...defEntry(def),
            key: 'drill:apps',
            description: `${connectors.length} app${connectors.length === 1 ? '' : 's'}`,
            picked: undefined,
            drillInto: 'apps' as const,
          }
        : defEntry(def),
    )

    const result: [string, PickerEntry[]][] = []
    if (recents.length > 0) result.push(['Recently used', recents])
    if (triggerDefs.length > 0) result.push(['Triggers', triggerDefs.map(defEntry)])
    result.push(['Core', coreEntries])
    return result
  }, [nodeDefs, connectors, query, view])

  const flatEntries = sections.flatMap(([, entries]) => entries)

  const activate = (entry: PickerEntry) => {
    if (entry.drillInto) {
      setView(entry.drillInto)
      setQuery('')
    } else if (entry.picked) {
      pick(entry.picked, entry.key)
    }
  }

  return (
    <>
      <div className="node-picker-backdrop" onClick={onClose} />
      <div className="node-picker">
        {view !== 'root' ? (
          <button
            type="button"
            className="node-picker-back"
            onClick={() => setView(view === 'apps' ? 'root' : 'apps')}
          >
            {view === 'apps' ? '← All nodes' : '← Apps'}
          </button>
        ) : (
          <input
            autoFocus
            placeholder="Search nodes and actions…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') onClose()
              if (event.key === 'Enter' && flatEntries.length > 0) activate(flatEntries[0])
            }}
          />
        )}
        <div className="node-picker-sections">
          {sections.map(([title, entries]) => (
            <div key={title}>
              <div className="node-picker-section">{title}</div>
              <ul>
                {entries.map((entry) => (
                  <li key={entry.key} onClick={() => activate(entry)}>
                    <span className="palette-icon" style={{ background: entry.iconBg }}>
                      {entry.icon}
                    </span>
                    <div>
                      <div className="node-picker-label">{entry.label}</div>
                      <div className="node-picker-description">{entry.description}</div>
                    </div>
                    {entry.drillInto && <span className="node-picker-arrow">→</span>}
                  </li>
                ))}
                {entries.length === 0 && <li className="node-picker-empty">No matching nodes</li>}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
