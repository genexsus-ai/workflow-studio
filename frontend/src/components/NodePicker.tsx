import { useEffect, useMemo, useState } from 'react'

import { listMcpServerTools } from '../api'
import { nodeIcon } from '../lib/nodeIcons'
import { loadRecents, saveRecent } from '../lib/recentPicks'
import type { AgentPresetDef, ConnectorDef, FlowPatternDef, McpServerSummary, McpToolInfo, NodeTypeDef, ToolDef } from '../types'

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
  drillInto?: ConnectorDef | 'apps' | 'agents' | 'tools' | 'mcp' | 'flows' | { mcpServer: string }
  picked?: PickedNode
}

interface NodePickerProps {
  nodeDefs: NodeTypeDef[]
  connectors?: ConnectorDef[]
  agentPresets?: AgentPresetDef[]
  tools?: ToolDef[]
  mcpServers?: McpServerSummary[]
  flows?: FlowPatternDef[]
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

function customToolEntry(def: NodeTypeDef): PickerEntry {
  return {
    ...defEntry(def),
    key: 'type:tool',
    label: 'Custom tool',
    description: 'Start from a blank tool node and choose the tool in the panel',
  }
}

function toolEntry(tool: ToolDef, toolColor: string): PickerEntry {
  return {
    key: `tool:${tool.name}`,
    icon: '🛠️',
    iconBg: `${toolColor}1c`,
    label: tool.name.replaceAll('_', ' '),
    description: tool.description,
    picked: {
      type: 'tool',
      config: { tool_name: tool.name, tool_params: {} },
      label: tool.name.replaceAll('_', ' '),
    },
  }
}

// Placeholder roles per pattern so a picked flow arrives with its
// structure visible — the user fills in roles/goals in the panel.
const FLOW_ROLE_SCAFFOLDS: Record<string, string[]> = {
  critic_review: ['Writer', 'Critic'],
  coordinator_worker: ['Coordinator', 'Worker'],
  delegator_worker: ['Delegator', 'Worker'],
  map_reduce: ['Worker', 'Combiner'],
  ensemble_voting: ['Voter 1', 'Voter 2'],
  auction: ['Bidder 1', 'Bidder 2'],
  p2p: ['Peer 1', 'Peer 2'],
  round_robin: ['Agent 1', 'Agent 2'],
  parallel: ['Agent 1', 'Agent 2'],
}

function flowPatternEntry(pattern: FlowPatternDef, color: string): PickerEntry {
  const roles = [...(FLOW_ROLE_SCAFFOLDS[pattern.id] ?? [])]
  while (roles.length < pattern.min_agents) roles.push(`Agent ${roles.length + 1}`)
  return {
    key: `flow:${pattern.id}`,
    icon: '👥',
    iconBg: `${color}1c`,
    label: pattern.label,
    description: `${pattern.description} ${pattern.order_hint}`,
    picked: {
      type: 'flow',
      config: {
        flow_type: pattern.id,
        agents: roles.map((role) => ({ role, goal: '' })),
        params: Object.fromEntries(
          pattern.params
            .filter((param) => param.default !== undefined)
            .map((param) => [param.name, param.default]),
        ),
      },
      label: pattern.label,
    },
  }
}

function mcpServerEntry(server: McpServerSummary, color: string): PickerEntry {
  return {
    key: `mcpserver:${server.name}`,
    icon: '🧩',
    iconBg: `${color}1c`,
    label: server.name,
    description: `${server.transport} · ${server.target}`,
    drillInto: { mcpServer: server.name },
  }
}

function mcpToolEntry(server: string, tool: McpToolInfo, color: string): PickerEntry {
  return {
    key: `mcptool:${server}:${tool.name}`,
    icon: '🧩',
    iconBg: `${color}1c`,
    label: tool.name.replaceAll('_', ' '),
    description: tool.description,
    picked: {
      type: 'mcp',
      config: { server, tool: tool.name, params: {} },
      label: tool.name.replaceAll('_', ' '),
    },
  }
}

function customAgentEntry(def: NodeTypeDef): PickerEntry {
  return {
    ...defEntry(def),
    key: 'type:agent',
    label: 'Custom agent',
    description: 'Start from a blank agent and write its role and goal yourself',
  }
}

function presetEntry(preset: AgentPresetDef, agentColor: string): PickerEntry {
  return {
    key: `preset:${preset.name}`,
    icon: '🤖',
    iconBg: `${agentColor}1c`,
    label: preset.role,
    description: preset.goal,
    picked: {
      type: 'agent',
      config: {
        role: preset.role,
        goal: preset.goal,
        backstory: preset.backstory,
        temperature: preset.temperature,
        tools: preset.tools,
      },
      label: preset.role,
    },
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
export function NodePicker({
  nodeDefs,
  connectors = [],
  agentPresets = [],
  tools = [],
  mcpServers = [],
  flows = [],
  onPick,
  onClose,
}: NodePickerProps) {
  const [query, setQuery] = useState('')
  const [view, setView] = useState<
    'root' | 'apps' | 'agents' | 'tools' | 'mcp' | 'flows' | ConnectorDef | { mcpServer: string }
  >('root')
  const [mcpToolCache, setMcpToolCache] = useState<
    Record<string, McpToolInfo[] | 'loading' | 'error'>
  >({})

  const mcpServerInView =
    typeof view === 'object' && 'mcpServer' in view ? view.mcpServer : null

  useEffect(() => {
    if (!mcpServerInView || mcpToolCache[mcpServerInView]) return
    setMcpToolCache((cache) => ({ ...cache, [mcpServerInView]: 'loading' }))
    listMcpServerTools(mcpServerInView)
      .then((fetched) =>
        setMcpToolCache((cache) => ({ ...cache, [mcpServerInView]: fetched })),
      )
      .catch(() =>
        setMcpToolCache((cache) => ({ ...cache, [mcpServerInView]: 'error' })),
      )
  }, [mcpServerInView, mcpToolCache])

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

    const agentDef = nodeDefs.find((def) => def.type === 'agent')
    const toolDef = nodeDefs.find((def) => def.type === 'tool')
    const mcpDef = nodeDefs.find((def) => def.type === 'mcp')
    const flowDef = nodeDefs.find((def) => def.type === 'flow')
    if (view === 'apps') {
      return [['Apps', connectors.map(appEntry)]]
    }
    if (view === 'agents') {
      const entries = agentDef ? [customAgentEntry(agentDef)] : []
      entries.push(...agentPresets.map((preset) => presetEntry(preset, agentDef?.color ?? '#8b5cf6')))
      return [['Agents', entries]]
    }
    if (view === 'tools') {
      const entries = toolDef ? [customToolEntry(toolDef)] : []
      entries.push(...tools.map((tool) => toolEntry(tool, toolDef?.color ?? '#f59e0b')))
      return [['Tools', entries]]
    }
    if (view === 'flows') {
      return [
        [
          'Collaboration patterns',
          flows.map((pattern) => flowPatternEntry(pattern, flowDef?.color ?? '#ec4899')),
        ],
      ]
    }
    if (view === 'mcp') {
      return [
        ['MCP servers', mcpServers.map((server) => mcpServerEntry(server, mcpDef?.color ?? '#10b981'))],
      ]
    }
    if (typeof view === 'object' && 'mcpServer' in view) {
      const cached = mcpToolCache[view.mcpServer]
      if (cached === 'loading' || cached === undefined) return [[view.mcpServer, []]]
      if (cached === 'error') return [[`${view.mcpServer} — could not load tools`, []]]
      return [
        [
          view.mcpServer,
          cached.map((tool) => mcpToolEntry(view.mcpServer, tool, mcpDef?.color ?? '#10b981')),
        ],
      ]
    }
    if (view !== 'root') {
      return [[view.label, Object.keys(view.actions).map((a) => actionEntry(view, a, false))]]
    }

    const needle = query.trim().toLowerCase()
    if (needle) {
      const matchingDefs = [...triggerDefs, ...coreDefs]
        .filter((def) => `${def.label} ${def.type} ${def.description}`.toLowerCase().includes(needle))
        .map(defEntry)
      const matchingPresets = agentPresets
        .filter((preset) =>
          `${preset.name} ${preset.role} ${preset.goal}`.toLowerCase().includes(needle),
        )
        .map((preset) => presetEntry(preset, agentDef?.color ?? '#8b5cf6'))
      const matchingTools = tools
        .filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(needle))
        .map((tool) => toolEntry(tool, toolDef?.color ?? '#f59e0b'))
      const matchingFlows = flows
        .filter((pattern) =>
          `${pattern.id} ${pattern.label} ${pattern.description}`.toLowerCase().includes(needle),
        )
        .map((pattern) => flowPatternEntry(pattern, flowDef?.color ?? '#ec4899'))
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
      return [['Results', [...matchingDefs, ...matchingPresets, ...matchingTools, ...matchingFlows, ...matchingActions]]]
    }

    const drillFor = (def: NodeTypeDef): PickerEntry | null => {
      if (hasApps && def.type === 'connector') {
        return {
          ...defEntry(def),
          key: 'drill:apps',
          description: `${connectors.length} app${connectors.length === 1 ? '' : 's'}`,
          picked: undefined,
          drillInto: 'apps' as const,
        }
      }
      if (agentPresets.length > 0 && def.type === 'agent') {
        return {
          ...defEntry(def),
          key: 'drill:agents',
          description: `Custom, or ${agentPresets.length} reusable role agents`,
          picked: undefined,
          drillInto: 'agents' as const,
        }
      }
      if (tools.length > 0 && def.type === 'tool') {
        return {
          ...defEntry(def),
          key: 'drill:tools',
          description: `Custom, or ${tools.length} built-in tools`,
          picked: undefined,
          drillInto: 'tools' as const,
        }
      }
      if (mcpServers.length > 0 && def.type === 'mcp') {
        return {
          ...defEntry(def),
          key: 'drill:mcp',
          description: `${mcpServers.length} server${mcpServers.length === 1 ? '' : 's'}`,
          picked: undefined,
          drillInto: 'mcp' as const,
        }
      }
      if (flows.length > 0 && def.type === 'flow') {
        return {
          ...defEntry(def),
          key: 'drill:flows',
          description: `${flows.length} collaboration patterns`,
          picked: undefined,
          drillInto: 'flows' as const,
        }
      }
      return null
    }

    const byKey = new Map<string, PickerEntry>()
    for (const def of [...triggerDefs, ...coreDefs]) {
      // Recents saved as plain type picks resolve to the drill entry when one
      // exists, so "Agent" opens the agent list from Recently used too.
      byKey.set(`type:${def.type}`, drillFor(def) ?? defEntry(def))
    }
    for (const app of connectors) {
      byKey.set(`app:${app.type}`, appEntry(app))
      for (const action of Object.keys(app.actions)) {
        byKey.set(`action:${app.type}:${action}`, actionEntry(app, action, true))
      }
    }
    for (const preset of agentPresets) {
      byKey.set(`preset:${preset.name}`, presetEntry(preset, agentDef?.color ?? '#8b5cf6'))
    }
    for (const tool of tools) {
      byKey.set(`tool:${tool.name}`, toolEntry(tool, toolDef?.color ?? '#f59e0b'))
    }
    for (const pattern of flows) {
      byKey.set(`flow:${pattern.id}`, flowPatternEntry(pattern, flowDef?.color ?? '#ec4899'))
    }
    const recents = loadRecents()
      .map((key) => byKey.get(key))
      .filter((entry): entry is PickerEntry => Boolean(entry))
      .slice(0, 4)

    const coreEntries = coreDefs.map((def) => drillFor(def) ?? defEntry(def))

    const result: [string, PickerEntry[]][] = []
    if (recents.length > 0) result.push(['Recently used', recents])
    if (triggerDefs.length > 0) result.push(['Triggers', triggerDefs.map(defEntry)])
    result.push(['Core', coreEntries])
    return result
  }, [nodeDefs, connectors, agentPresets, tools, mcpServers, flows, mcpToolCache, query, view])

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
            onClick={() =>
              setView(
                typeof view === 'string' ? 'root' : 'mcpServer' in view ? 'mcp' : 'apps',
              )
            }
          >
            {typeof view === 'string'
              ? '← All nodes'
              : 'mcpServer' in view
                ? '← MCP servers'
                : '← Apps'}
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
                {entries.length === 0 && (
                  <li className="node-picker-empty">
                    {mcpServerInView && mcpToolCache[mcpServerInView] !== 'error'
                      ? 'Loading tools…'
                      : 'No matching nodes'}
                  </li>
                )}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
