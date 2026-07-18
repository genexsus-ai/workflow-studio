import {
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Edge,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  type OnSelectionChangeFunc,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useState } from 'react'

import * as api from './api'
import { AppSidebar, type AppId } from './components/AppSidebar'
import { AutomationPanel } from './components/AutomationPanel'
import type { PickedNode } from './components/NodePicker'
import { Canvas } from './components/Canvas'
import { NodeIOView } from './components/NodeIOView'
import type { UpstreamEntry } from './components/NodeIOView'
import { ConfigPanel } from './components/ConfigPanel'
import { DataScienceView } from './components/DataScienceView'
import { DatasetsView } from './components/DatasetsView'
import { InsightsView } from './components/InsightsView'
import { CredentialsPanel } from './components/CredentialsPanel'
import { GenerateDialog } from './components/GenerateDialog'
import { McpServersPanel } from './components/McpServersPanel'
import { RunPanel } from './components/RunPanel'
import { RunsPanel } from './components/RunsPanel'
import { VersionsPanel } from './components/VersionsPanel'
import { Toolbar } from './components/Toolbar'
import { EXPANDABLE_PATTERNS } from './lib/flowExpansion'
import { ATTACH_EDGE_STYLE, docToFlow, flowToDoc, nextNodeId, type StudioNode } from './lib/translate'
import type { AutomationConfig, CredentialSummary, McpServerSummary, NodeResult, NodeRunStatus, Palette as PaletteData, RunEvent } from './types'

export default function App() {
  const [palette, setPalette] = useState<PaletteData | null>(null)
  const [nodes, setNodes] = useState<StudioNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [workflowName, setWorkflowName] = useState('Untitled workflow')
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [workflows, setWorkflows] = useState<Awaited<ReturnType<typeof api.listWorkflows>>>([])
  const [selectedNode, setSelectedNode] = useState<StudioNode | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null)
  const [runOpen, setRunOpen] = useState(false)
  const [generateOpen, setGenerateOpen] = useState(false)
  const [lastGenerationId, setLastGenerationId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [activeApp, setActiveApp] = useState<AppId>('workflow')
  const [insightsOpen, setInsightsOpen] = useState(false)
  const [pinnedInput, setPinnedInput] = useState<Record<string, unknown> | null>(null)
  const [runEvents, setRunEvents] = useState<RunEvent[]>([])
  const [nodeResults, setNodeResults] = useState<Record<string, NodeResult>>({})
  const [lastRunInput, setLastRunInput] = useState<Record<string, unknown> | null>(null)
  const [ioNodeId, setIoNodeId] = useState<string | null>(null)
  const defaultAutomation: AutomationConfig = {
    webhook_enabled: false,
    webhook_token: null,
    schedule_enabled: false,
    interval_seconds: 300,
    schedule_cron: null,
    schedule_timezone: 'UTC',
  }
  const [automation, setAutomation] = useState<AutomationConfig>(defaultAutomation)
  const [sharedMemory, setSharedMemory] = useState(false)
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
  const [saveCount, setSaveCount] = useState(0)
  const [credentials, setCredentials] = useState<CredentialSummary[]>([])
  const [mcpServers, setMcpServers] = useState<McpServerSummary[]>([])
  const [runError, setRunError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [banner, setBanner] = useState<string | null>(null)

  const colorFor = useCallback(
    (type: string) => palette?.node_types.find((t) => t.type === type)?.color,
    [palette],
  )

  const refreshWorkflows = useCallback(() => {
    api.listWorkflows().then(setWorkflows).catch(() => setWorkflows([]))
  }, [])

  const refreshCredentials = useCallback(() => {
    api.listCredentials().then(setCredentials).catch(() => setCredentials([]))
  }, [])

  const refreshMcpServers = useCallback(() => {
    api.listMcpServers().then(setMcpServers).catch(() => setMcpServers([]))
    // MCP servers contribute mcp__* agent tools, so the palette changes too
    api.fetchPalette().then(setPalette).catch(() => {})
  }, [])

  useEffect(() => {
    api
      .fetchPalette()
      .then(setPalette)
      .catch(() => setBanner('Backend unreachable — start it with restart_workflow_studio_backend.sh'))
    refreshWorkflows()
    refreshCredentials()
    refreshMcpServers()
  }, [refreshWorkflows, refreshCredentials, refreshMcpServers])

  const onNodesChange: OnNodesChange<StudioNode> = useCallback(
    (changes) => {
      setNodes((current) => applyNodeChanges(changes, current))
      if (changes.some((c) => c.type !== 'select' && c.type !== 'dimensions')) setDirty(true)
    },
    [],
  )

  const onEdgesChange: OnEdgesChange = useCallback((changes) => {
    setEdges((current) => applyEdgeChanges(changes, current))
    if (changes.some((c) => c.type !== 'select')) setDirty(true)
  }, [])

  const onConnect: OnConnect = useCallback((connection) => {
    const isAttachSource = connection.sourceHandle === 'attach'
    const attachKind = connection.targetHandle?.startsWith('attach_')
      ? connection.targetHandle.slice('attach_'.length)
      : null
    // Capability handles only pair with each other: top handle -> agent port
    if (isAttachSource !== Boolean(attachKind)) return
    setEdges((current) => {
      const added = addEdge(
        { ...connection, data: { condition: null, parallel: false, attach: attachKind } },
        current,
      )
      return attachKind
        ? added.map((edge) =>
            edge.data?.attach ? { ...edge, style: ATTACH_EDGE_STYLE } : edge,
          )
        : added
    })
    setDirty(true)
  }, [])

  const onSelectionChange: OnSelectionChangeFunc = useCallback((selection) => {
    setSelectedNode((selection.nodes[0] as StudioNode | undefined) ?? null)
    setSelectedEdge(selection.edges[0] ?? null)
  }, [])

  const buildNode = useCallback(
    (picked: PickedNode, position: { x: number; y: number }, taken: Set<string>): StudioNode => {
      const id = nextNodeId(picked.type, taken)
      const def = palette?.node_types.find((t) => t.type === picked.type)
      const defaults: Record<string, unknown> = {}
      def?.config_fields.forEach((field) => {
        if (field.default !== undefined) defaults[field.name] = field.default
      })
      const base = picked.label ?? def?.label
      return {
        id,
        type: 'studio',
        position,
        data: {
          nodeType: picked.type,
          label: base ? `${base} ${id.split('_').pop()}` : id,
          config: { ...defaults, ...(picked.config ?? {}) },
          status: 'idle',
          color: colorFor(picked.type) ?? '#64748b',
        },
      }
    },
    [palette, colorFor],
  )

  // Flow picks carrying a team scaffold materialize on the canvas. Graph-
  // shaped patterns (round robin, coordinator/workers, map-reduce, parallel)
  // expand into REAL wired agent nodes so the collaboration is visible;
  // logic-shaped patterns (critic loops, voting, ...) become a team node
  // with agents attached to its Agents port. entryIds are the nodes an
  // upstream edge should connect to.
  const expandFlowTeam = useCallback(
    (
      picked: PickedNode,
      position: { x: number; y: number },
      taken: Set<string>,
    ): { nodes: StudioNode[]; edges: Edge[]; entryIds: string[] } => {
      const teamSpecs =
        picked.type === 'flow' && Array.isArray(picked.config?.agents)
          ? (picked.config.agents as {
              role: string
              goal: string
              backstory?: string
              temperature?: number
            }[])
          : []
      if (teamSpecs.length === 0) {
        const node = buildNode(picked, position, taken)
        return { nodes: [node], edges: [], entryIds: [node.id] }
      }

      const buildAgent = (
        spec: (typeof teamSpecs)[number],
        pos: { x: number; y: number },
        ids: Set<string>,
      ): StudioNode => {
        const config: Record<string, unknown> = { role: spec.role, goal: spec.goal }
        if (spec.backstory) config.backstory = spec.backstory
        if (spec.temperature !== undefined) config.temperature = spec.temperature
        const node = buildNode({ type: 'agent', config, label: spec.role }, pos, ids)
        ids.add(node.id)
        return node
      }

      const flowType = String(picked.config?.flow_type ?? '')
      const shape = EXPANDABLE_PATTERNS[flowType]
      const ids = new Set(taken)

      if (shape) {
        const flowEdge = (source: string, target: string, parallel = false): Edge =>
          ({
            id: `e_${source}_${target}`,
            source,
            target,
            sourceHandle: null,
            targetHandle: null,
            animated: parallel,
            data: { condition: null, parallel, attach: null },
          }) as Edge
        const agents: StudioNode[] = []
        const edges: Edge[] = []

        if (shape === 'chain') {
          teamSpecs.forEach((spec, i) => {
            agents.push(buildAgent(spec, { x: position.x + i * 260, y: position.y }, ids))
            if (i > 0) edges.push(flowEdge(agents[i - 1].id, agents[i].id))
          })
          return { nodes: agents, edges, entryIds: [agents[0].id] }
        }
        if (shape === 'fan_out') {
          const [head, ...rest] = teamSpecs
          const coordinator = buildAgent(head, position, ids)
          agents.push(coordinator)
          rest.forEach((spec, i) => {
            const worker = buildAgent(
              spec,
              { x: position.x + 280, y: position.y - 80 + i * 170 },
              ids,
            )
            agents.push(worker)
            edges.push(flowEdge(coordinator.id, worker.id, rest.length > 1))
          })
          return { nodes: agents, edges, entryIds: [coordinator.id] }
        }
        if (shape === 'fan_in') {
          const workers = teamSpecs.slice(0, -1)
          const reducerSpec = teamSpecs[teamSpecs.length - 1]
          const workerNodes = workers.map((spec, i) =>
            buildAgent(spec, { x: position.x, y: position.y - 80 + i * 170 }, ids),
          )
          const reducer = buildAgent(reducerSpec, { x: position.x + 280, y: position.y }, ids)
          workerNodes.forEach((worker) => edges.push(flowEdge(worker.id, reducer.id)))
          return {
            nodes: [...workerNodes, reducer],
            edges,
            entryIds: workerNodes.map((worker) => worker.id),
          }
        }
        // parallel: side-by-side, upstream fans into all of them
        teamSpecs.forEach((spec, i) => {
          agents.push(buildAgent(spec, { x: position.x, y: position.y - 80 + i * 170 }, ids))
        })
        return { nodes: agents, edges, entryIds: agents.map((agent) => agent.id) }
      }

      // Logic-shaped pattern: one team node with attached member agents.
      // Two-tier layout: agent 1 (the lead — delegator/writer/...) sits
      // directly under the Agents port; the rest form a worker row below.
      const flowNode = buildNode(
        { ...picked, config: { ...picked.config, agents: [] } },
        position,
        ids,
      )
      ids.add(flowNode.id)
      const outNodes: StudioNode[] = [flowNode]
      const outEdges: Edge[] = []
      teamSpecs.forEach((spec, index) => {
        const workerCount = teamSpecs.length - 1
        const pos =
          index === 0
            ? { x: position.x + 20, y: position.y + 150 }
            : {
                x: position.x + 20 - ((workerCount - 1) * 240) / 2 + (index - 1) * 240,
                y: position.y + 320,
              }
        const agentNode = buildAgent(spec, pos, ids)
        outNodes.push(agentNode)
        outEdges.push({
          id: `attach_${agentNode.id}_${flowNode.id}`,
          source: agentNode.id,
          target: flowNode.id,
          sourceHandle: 'attach',
          targetHandle: 'attach_agents',
          style: ATTACH_EDGE_STYLE,
          data: { condition: null, parallel: false, attach: 'agents' },
        } as Edge)
      })
      return { nodes: outNodes, edges: outEdges, entryIds: [flowNode.id] }
    },
    [buildNode],
  )

  const onDropNode = useCallback(
    (picked: PickedNode, position: { x: number; y: number }) => {
      const added = expandFlowTeam(picked, position, new Set(nodes.map((n) => n.id)))
      setNodes((current) => [...current, ...added.nodes])
      if (added.edges.length > 0) setEdges((current) => [...current, ...added.edges])
      setDirty(true)
    },
    [nodes, expandFlowTeam],
  )

  const onAddConnected = useCallback(
    (sourceId: string, picked: PickedNode) => {
      const source = nodes.find((node) => node.id === sourceId)
      if (!source) return
      const added = expandFlowTeam(
        picked,
        { x: source.position.x + 260, y: source.position.y },
        new Set(nodes.map((n) => n.id)),
      )
      setNodes((current) => [...current, ...added.nodes])
      const fanOut = added.entryIds.length > 1
      setEdges((current) => {
        let next = current
        for (const entryId of added.entryIds) {
          next = addEdge(
            {
              source: sourceId,
              target: entryId,
              sourceHandle: null,
              targetHandle: null,
              animated: fanOut,
              data: { condition: null, parallel: fanOut, attach: null },
            },
            next,
          )
        }
        return [...next, ...added.edges]
      })
      setDirty(true)
    },
    [nodes, expandFlowTeam],
  )

  const onAddAttached = useCallback(
    (agentId: string, port: 'model' | 'memory' | 'tools' | 'agents', picked: PickedNode) => {
      const agent = nodes.find((node) => node.id === agentId)
      if (!agent) return
      const portOffset = { model: -50, memory: 45, tools: 140, agents: 45 }[port]
      // Tools ports hold many attachments: fan additional ones out to the right.
      const siblings = edges.filter(
        (edge) => edge.target === agentId && edge.targetHandle === `attach_${port}`,
      ).length
      const node = buildNode(
        picked,
        {
          x: agent.position.x + portOffset + siblings * 200,
          y: agent.position.y + 140 + siblings * 24,
        },
        new Set(nodes.map((n) => n.id)),
      )
      setNodes((current) => [...current, node])
      setEdges((current) =>
        addEdge(
          {
            source: node.id,
            target: agentId,
            sourceHandle: 'attach',
            targetHandle: `attach_${port}`,
            style: ATTACH_EDGE_STYLE,
            data: { condition: null, parallel: false, attach: port },
          },
          current,
        ),
      )
      setDirty(true)
    },
    [nodes, edges, buildNode],
  )

  const onNodeConfigChange = useCallback(
    (nodeId: string, config: Record<string, unknown>, label?: string) => {
      setNodes((current) =>
        current.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: { ...node.data, config, label: label ?? node.data.label },
              }
            : node,
        ),
      )
      setSelectedNode((node) =>
        node && node.id === nodeId
          ? { ...node, data: { ...node.data, config, label: label ?? node.data.label } }
          : node,
      )
      setDirty(true)
    },
    [],
  )

  const onEdgeChange = useCallback(
    (edgeId: string, data: { condition: string | null; parallel: boolean }) => {
      setEdges((current) =>
        current.map((edge) =>
          edge.id === edgeId
            ? { ...edge, data, label: data.condition ?? undefined, animated: data.parallel }
            : edge,
        ),
      )
      setSelectedEdge((edge) => (edge && edge.id === edgeId ? { ...edge, data } : edge))
      setDirty(true)
    },
    [],
  )

  const onDeleteNode = useCallback((nodeId: string) => {
    setNodes((current) => current.filter((node) => node.id !== nodeId))
    setEdges((current) =>
      current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    )
    setSelectedNode(null)
    setDirty(true)
  }, [])

  const onDeleteEdge = useCallback((edgeId: string) => {
    setEdges((current) => current.filter((edge) => edge.id !== edgeId))
    setSelectedEdge(null)
    setDirty(true)
  }, [])

  const currentDoc = useMemo(
    () => ({
      ...flowToDoc(workflowName, '', nodes, edges, currentId),
      automation,
      shared_memory: sharedMemory,
      pinned_input: pinnedInput,
    }),
    [workflowName, nodes, edges, currentId, automation, sharedMemory, pinnedInput],
  )

  // Input fields the workflow references via {{ input.* }} templates —
  // used to pre-fill the Run dialog with the right JSON skeleton.
  const suggestedInput = useMemo(() => {
    const serialized = JSON.stringify(nodes.map((node) => node.data.config))
    const keys = new Set<string>()
    for (const match of serialized.matchAll(/\{\{\s*input\.([A-Za-z0-9_]+)/g)) {
      keys.add(match[1])
    }
    if (keys.size === 0) return null
    return Object.fromEntries([...keys].map((key) => [key, '']))
  }, [nodes])

  const onSave = useCallback(async () => {
    try {
      const saved = currentId
        ? await api.updateWorkflow(currentId, currentDoc)
        : await api.createWorkflow(currentDoc)
      setCurrentId(saved.id ?? null)
      // The backend derives automation from trigger nodes on save (e.g. minting
      // a form/webhook token) — reflect what it actually persisted.
      setAutomation(saved.automation ?? defaultAutomation)
      setDirty(false)
      setBanner(`Saved "${saved.name}"`)
      setSaveCount((count) => count + 1)
      refreshWorkflows()
      if (lastGenerationId) {
        // Saving a generated draft marks it accepted so future generations
        // learn from it; best-effort, failures are invisible to the user.
        api.acceptGeneration(lastGenerationId).catch(() => undefined)
        setLastGenerationId(null)
      }
    } catch (err) {
      setBanner(`Save failed: ${(err as Error).message}`)
    }
  }, [currentId, currentDoc, refreshWorkflows, lastGenerationId])

  const onLoad = useCallback(
    async (id: string) => {
      try {
        const doc = await api.getWorkflow(id)
        const flow = docToFlow(doc, colorFor)
        setNodes(flow.nodes)
        setEdges(flow.edges)
        setWorkflowName(doc.name)
        setCurrentId(doc.id ?? id)
        setAutomation(doc.automation ?? defaultAutomation)
        setSharedMemory(Boolean(doc.shared_memory))
        setPinnedInput(doc.pinned_input ?? null)
        setDirty(false)
        setSelectedNode(null)
        setSelectedEdge(null)
      } catch (err) {
        setBanner(`Load failed: ${(err as Error).message}`)
      }
    },
    [colorFor],
  )

  const onImportYaml = useCallback(
    async (yamlText: string) => {
      try {
        const doc = await api.importWorkflowYaml(yamlText)
        const flow = docToFlow(doc, colorFor)
        setNodes(flow.nodes)
        setEdges(flow.edges)
        setWorkflowName(doc.name)
        setCurrentId(doc.id ?? null)
        setAutomation(doc.automation ?? defaultAutomation)
        setSharedMemory(Boolean(doc.shared_memory))
        setPinnedInput(doc.pinned_input ?? null)
        setDirty(false)
        setSelectedNode(null)
        setSelectedEdge(null)
        refreshWorkflows()
        setBanner(`Imported "${doc.name}"`)
      } catch (err) {
        setBanner(`Import failed: ${(err as Error).message}`)
      }
    },
    [colorFor, refreshWorkflows],
  )

  const onGenerated = useCallback(
    (result: api.GenerateResult) => {
      const doc = result.workflow
      const flow = docToFlow(doc, colorFor)
      setNodes(flow.nodes)
      setEdges(flow.edges)
      setWorkflowName(doc.name)
      setCurrentId(null)
      setLastGenerationId(result.generation_id)
      setAutomation(doc.automation ?? defaultAutomation)
      setSharedMemory(Boolean(doc.shared_memory))
      setPinnedInput(doc.pinned_input ?? null)
      setDirty(true)
      setSelectedNode(null)
      setSelectedEdge(null)
      const notes: string[] = [`Generated draft "${doc.name}" — review and save`]
      if (result.open_questions.length > 0) {
        notes.push(
          `${result.open_questions.length} open question${result.open_questions.length === 1 ? '' : 's'}: ` +
            result.open_questions.map((question) => question.question).join(' | '),
        )
      }
      if (result.review && !result.review.approved) {
        notes.push(`Reviewer concerns: ${result.review.issues.join(' | ')}`)
      }
      const validationErrors = result.validation?.issues?.filter(
        (issue) => issue.level === 'error',
      )
      if (validationErrors && validationErrors.length > 0) {
        notes.push(
          `Validation: ${validationErrors.map((issue) => issue.message).join(' | ')}`,
        )
      }
      setBanner(notes.join(' — '))
    },
    [colorFor],
  )

  const onNew = useCallback(() => {
    setNodes([])
    setEdges([])
    setWorkflowName('Untitled workflow')
    setCurrentId(null)
    setSelectedNode(null)
    setSelectedEdge(null)
    setAutomation(defaultAutomation)
    setPinnedInput(null)
    setDirty(false)
  }, [])

  const onDelete = useCallback(async () => {
    if (!currentId) return
    await api.deleteWorkflow(currentId)
    refreshWorkflows()
    onNew()
  }, [currentId, refreshWorkflows, onNew])

  const setNodeStatus = useCallback((nodeId: string, status: NodeRunStatus) => {
    setNodes((current) =>
      current.map((node) =>
        node.id === nodeId ? { ...node, data: { ...node.data, status } } : node,
      ),
    )
  }, [])

  const onStartRun = useCallback(
    async (input: Record<string, unknown>, modelOverride?: string) => {
      setRunning(true)
      setRunError(null)
      setRunEvents([])
      setNodeResults({})
      setLastRunInput(input)
      setNodes((current) =>
        current.map((node) => ({ ...node, data: { ...node.data, status: 'idle' as const } })),
      )
      try {
        await api.streamRun(currentDoc, input, (event) => {
          setRunEvents((current) => [...current, event])
          if (event.event === 'node') {
            setNodeStatus(String(event.data.node_id), event.data.status as NodeRunStatus)
          }
          if (event.event === 'complete' || event.event === 'error') {
            const result = event.data.result as { node_results?: Record<string, NodeResult> } | undefined
            if (result?.node_results) setNodeResults(result.node_results)
          }
        }, modelOverride)
      } catch (err) {
        setRunError((err as Error).message)
      } finally {
        setRunning(false)
        setRunsRefreshKey((k) => k + 1)
      }
    },
    [currentDoc, setNodeStatus],
  )

  // n8n-style node I/O: a node's "input" is the output of its incoming
  // edges' source nodes (or the run input for entry nodes).
  const upstreamFor = useCallback(
    (nodeId: string): UpstreamEntry[] =>
      edges
        .filter((edge) => edge.target === nodeId)
        .map((edge) => {
          const source = nodes.find((n) => n.id === edge.source)
          return {
            id: edge.source,
            label: source?.data.label ?? edge.source,
            result: nodeResults[edge.source],
          }
        }),
    [edges, nodes, nodeResults],
  )
  const ioNode = ioNodeId ? (nodes.find((n) => n.id === ioNodeId) ?? null) : null

  const onAutomationChange = useCallback(
    async (config: AutomationConfig) => {
      if (!currentId) return
      try {
        const saved = await api.updateAutomation(currentId, config)
        setAutomation(saved.automation ?? config)
        setBanner(
          config.schedule_enabled || config.webhook_enabled
            ? 'Automation updated'
            : 'Automation disabled',
        )
      } catch (err) {
        setBanner(`Automation failed: ${(err as Error).message}`)
      }
    },
    [currentId],
  )

  return (
    <ReactFlowProvider>
      <div className="app-shell">
        <AppSidebar active={activeApp} onSelect={setActiveApp} />
        {activeApp === 'analytics' ? (
          <div className="app">
            <DatasetsView />
          </div>
        ) : activeApp === 'datascience' ? (
          <div className="app">
            <DataScienceView />
          </div>
        ) : (
      <div className="app">
        <Toolbar
          workflowName={workflowName}
          workflows={workflows}
          currentId={currentId}
          running={running}
          dirty={dirty}
          onNameChange={(name) => {
            setWorkflowName(name)
            setDirty(true)
          }}
          onNew={onNew}
          onSave={onSave}
          onLoad={onLoad}
          onDelete={onDelete}
          onImportYaml={onImportYaml}
          onRun={() => setRunOpen(true)}
          onGenerate={() => setGenerateOpen(true)}
          insightsOpen={insightsOpen}
          onToggleInsights={() => setInsightsOpen((open) => !open)}
        />
        <GenerateDialog
          open={generateOpen}
          onClose={() => setGenerateOpen(false)}
          onGenerated={onGenerated}
          currentDoc={nodes.length > 0 ? currentDoc : null}
        />
        {ioNode && (
          <NodeIOView
            node={ioNode}
            upstream={upstreamFor(ioNode.id)}
            workflowInput={lastRunInput}
            result={nodeResults[ioNode.id]}
            onClose={() => setIoNodeId(null)}
          />
        )}
        {banner && (
          <div className="banner" onClick={() => setBanner(null)}>
            {banner}
          </div>
        )}
        {insightsOpen ? (
          <div className="workspace">
            <InsightsView />
          </div>
        ) : (
        <div className="workspace">
          <Canvas
            nodes={nodes}
            edges={edges}
            nodeDefs={palette?.node_types ?? []}
            connectors={palette?.connectors ?? []}
            agentPresets={palette?.agent_presets ?? []}
            tools={palette?.tools ?? []}
            mcpServers={mcpServers}
            flows={palette?.flows ?? []}
            onAddConnected={onAddConnected}
            onAddAttached={onAddAttached}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onSelectionChange={onSelectionChange}
            onNodeOpenIO={setIoNodeId}
            onDropNode={onDropNode}
          />
          <div className="right-rail">
            <ConfigPanel
              node={selectedNode}
              edge={selectedEdge}
              nodeResult={selectedNode ? nodeResults[selectedNode.id] : undefined}
              upstream={selectedNode ? upstreamFor(selectedNode.id) : []}
              workflowInput={lastRunInput}
              onOpenIO={setIoNodeId}
              automation={automation}
              nodeTypes={palette?.node_types ?? []}
              tools={palette?.tools ?? []}
              models={palette?.models ?? []}
              connectors={palette?.connectors ?? []}
              credentials={credentials}
              mcpServers={mcpServers}
              workflows={workflows}
              flows={palette?.flows ?? []}
              currentWorkflowId={currentId}
              onNodeConfigChange={onNodeConfigChange}
              onEdgeChange={onEdgeChange}
              onDeleteNode={onDeleteNode}
              onDeleteEdge={onDeleteEdge}
            />
            <RunPanel
              open={runOpen}
              running={running}
              events={runEvents}
              error={runError}
              models={palette?.models ?? []}
              suggestedInput={suggestedInput}
              pinnedInput={pinnedInput}
              onPin={(input) => {
                setPinnedInput(input)
                setDirty(true)
                setBanner(
                  input
                    ? 'Input pinned — save the workflow to persist it'
                    : 'Pinned input removed — save the workflow to persist',
                )
              }}
              onClose={() => setRunOpen(false)}
              onStart={onStartRun}
            />
            <CredentialsPanel
              connectors={palette?.connectors ?? []}
              credentials={credentials}
              onChanged={refreshCredentials}
            />
            <McpServersPanel servers={mcpServers} onChanged={refreshMcpServers} />
            <AutomationPanel
              workflowId={currentId}
              automation={automation}
              workflows={workflows}
              onChange={onAutomationChange}
              hasTrigger={nodes.some((node) => node.data.nodeType === 'trigger')}
              sharedMemory={sharedMemory}
              onSharedMemoryChange={(value) => {
                setSharedMemory(value)
                setDirty(true)
              }}
            />
            <VersionsPanel
              workflowId={currentId}
              refreshKey={saveCount}
              onRestored={(id) => {
                void onLoad(id)
                setBanner('Version restored')
              }}
            />
            <RunsPanel refreshKey={runsRefreshKey} />
          </div>
        </div>
        )}
      </div>
        )}
      </div>
    </ReactFlowProvider>
  )
}
