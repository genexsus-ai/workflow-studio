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
import { AutomationPanel } from './components/AutomationPanel'
import { Canvas } from './components/Canvas'
import { ConfigPanel } from './components/ConfigPanel'
import { CredentialsPanel } from './components/CredentialsPanel'
import { GenerateDialog } from './components/GenerateDialog'
import { McpServersPanel } from './components/McpServersPanel'
import { Palette } from './components/Palette'
import { RunPanel } from './components/RunPanel'
import { RunsPanel } from './components/RunsPanel'
import { Toolbar } from './components/Toolbar'
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
  const [runEvents, setRunEvents] = useState<RunEvent[]>([])
  const [nodeResults, setNodeResults] = useState<Record<string, NodeResult>>({})
  const defaultAutomation: AutomationConfig = {
    webhook_enabled: false,
    webhook_token: null,
    schedule_enabled: false,
    interval_seconds: 300,
  }
  const [automation, setAutomation] = useState<AutomationConfig>(defaultAutomation)
  const [sharedMemory, setSharedMemory] = useState(false)
  const [runsRefreshKey, setRunsRefreshKey] = useState(0)
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

  const onDropNode = useCallback(
    (type: string, position: { x: number; y: number }) => {
      const taken = new Set(nodes.map((n) => n.id))
      const id = nextNodeId(type, taken)
      const def = palette?.node_types.find((t) => t.type === type)
      const defaults: Record<string, unknown> = {}
      def?.config_fields.forEach((field) => {
        if (field.default !== undefined) defaults[field.name] = field.default
      })
      const node: StudioNode = {
        id,
        type: 'studio',
        position,
        data: {
          nodeType: type,
          label: def?.label ? `${def.label} ${id.split('_').pop()}` : id,
          config: defaults,
          status: 'idle',
          color: colorFor(type) ?? '#64748b',
        },
      }
      setNodes((current) => [...current, node])
      setDirty(true)
    },
    [nodes, palette, colorFor],
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
    }),
    [workflowName, nodes, edges, currentId, automation, sharedMemory],
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
      setDirty(false)
      setBanner(`Saved "${saved.name}"`)
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
        />
        <GenerateDialog
          open={generateOpen}
          onClose={() => setGenerateOpen(false)}
          onGenerated={onGenerated}
          currentDoc={nodes.length > 0 ? currentDoc : null}
        />
        {banner && (
          <div className="banner" onClick={() => setBanner(null)}>
            {banner}
          </div>
        )}
        <div className="workspace">
          <Palette nodeTypes={palette?.node_types ?? []} />
          <Canvas
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onSelectionChange={onSelectionChange}
            onDropNode={onDropNode}
          />
          <div className="right-rail">
            <ConfigPanel
              node={selectedNode}
              edge={selectedEdge}
              nodeResult={selectedNode ? nodeResults[selectedNode.id] : undefined}
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
              onChange={onAutomationChange}
              sharedMemory={sharedMemory}
              onSharedMemoryChange={(value) => {
                setSharedMemory(value)
                setDirty(true)
              }}
            />
            <RunsPanel refreshKey={runsRefreshKey} />
          </div>
        </div>
      </div>
    </ReactFlowProvider>
  )
}
