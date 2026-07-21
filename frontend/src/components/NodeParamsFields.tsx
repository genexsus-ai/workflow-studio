import { useEffect, useState } from 'react'

import { apiBase, listMcpServerTools, testNode } from '../api'

import { Combobox } from './Combobox'
import { FormFieldsEditor } from './AutomationPanel'

import type { StudioNode } from '../lib/translate'
import type {
  AutomationConfig,
  ConfigField,
  ConnectorDef,
  CredentialSummary,
  FlowAgentSpec,
  FlowPatternDef,
  FormField,
  McpServerSummary,
  McpToolInfo,
  ModelOption,
  NodeTestResult,
  NodeTypeDef,
  ToolDef,
  WorkflowSummary,
} from '../types'

export interface NodeParamsFieldsProps {
  node: StudioNode
  def: NodeTypeDef | undefined
  automation?: AutomationConfig
  onSaveWorkflow?: () => void
  /** Shows an "Open form editor" shortcut button (rail only — the dialog IS the editor). */
  onOpenIO?: (nodeId: string) => void
  /** Hide the inline "Test this node" button — the dialog uses its header
   * "Execute step" button instead, so the inline one would be redundant. */
  hideNodeTest?: boolean
  tools: ToolDef[]
  models: ModelOption[]
  connectors: ConnectorDef[]
  credentials: CredentialSummary[]
  mcpServers: McpServerSummary[]
  workflows: WorkflowSummary[]
  flows: FlowPatternDef[]
  currentWorkflowId: string | null
  onNodeConfigChange: (nodeId: string, config: Record<string, unknown>, label?: string) => void
}

/**
 * The parameter-editing body of a node's inspector — label, type-specific
 * config fields, and per-type extras (flow pattern settings, tool/connector/
 * MCP param hints, execution policy, node test). Shared between the rail
 * ConfigPanel and the n8n-style Input | Parameters | Output detail dialog so
 * both stay in sync automatically.
 */
export function NodeParamsFields({
  node,
  def,
  automation,
  onSaveWorkflow,
  onOpenIO,
  hideNodeTest,
  tools,
  models,
  connectors,
  credentials,
  mcpServers,
  workflows,
  flows,
  currentWorkflowId,
  onNodeConfigChange,
}: NodeParamsFieldsProps) {
  const config = node.data.config

  const setValue = (name: string, value: unknown) => {
    onNodeConfigChange(node.id, { ...config, [name]: value })
  }

  // A trigger is a schedule, webhook, or form; show only the fields that
  // apply to the selected kind (the backend ignores the rest anyway).
  const isTriggerFieldVisible = (field: ConfigField) => {
    if (node.data.nodeType !== 'trigger') return true
    const kind = (config.trigger_kind as string) ?? 'schedule'
    const provider = (config.webhook_provider as string) ?? 'generic'
    const scheduleOnly = ['interval_seconds', 'cron', 'timezone']
    const githubOnly = ['webhook_event_filter', 'webhook_secret']
    const webhookOnly = ['webhook_provider', 'webhook_respond_mode', ...githubOnly]
    const formOnly = ['form_title', 'form_description']
    if (kind === 'manual')
      return ![...scheduleOnly, ...webhookOnly, ...formOnly].includes(field.name)
    if (kind === 'form') return ![...scheduleOnly, ...webhookOnly].includes(field.name)
    if (kind !== 'webhook') return ![...webhookOnly, ...formOnly].includes(field.name)
    if ([...scheduleOnly, ...formOnly].includes(field.name)) return false
    // Signature secret and event filter only apply to signed GitHub webhooks.
    if (githubOnly.includes(field.name)) return provider === 'github'
    return true
  }

  const renderField = (field: ConfigField) => {
    const value = config[field.name] ?? field.default ?? ''
    switch (field.type) {
      case 'password':
        return (
          <input
            type="password"
            value={String(value)}
            placeholder={field.placeholder}
            onChange={(event) => setValue(field.name, event.target.value)}
          />
        )
      case 'text':
        return (
          <textarea
            value={String(value)}
            placeholder={field.placeholder}
            rows={3}
            onChange={(event) => setValue(field.name, event.target.value)}
          />
        )
      case 'number':
        return (
          <input
            type="number"
            value={Number(value)}
            min={field.min}
            max={field.max}
            step={0.1}
            onChange={(event) => setValue(field.name, Number(event.target.value))}
          />
        )
      case 'boolean':
        return (
          <input
            type="checkbox"
            checked={Boolean(config[field.name] ?? field.default ?? false)}
            onChange={(event) => setValue(field.name, event.target.checked)}
          />
        )
      case 'select':
        return (
          <select value={String(value)} onChange={(event) => setValue(field.name, event.target.value)}>
            {(field.options ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        )
      case 'model_select':
        return (
          <select value={String(value)} onChange={(event) => setValue(field.name, event.target.value)}>
            {models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
        )
      case 'workflow_select':
        return (
          <select value={String(value)} onChange={(event) => setValue(field.name, event.target.value)}>
            <option value="">Select workflow…</option>
            {workflows
              .filter((workflow) => workflow.id !== currentWorkflowId)
              .map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name}
                </option>
              ))}
          </select>
        )
      case 'connector_select':
        return (
          <Combobox
            value={String(value)}
            placeholder="— choose integration —"
            options={connectors.map((c) => ({
              value: c.type,
              label: c.label,
              description: `${Object.keys(c.actions).length} actions`,
            }))}
            onChange={(next) =>
              onNodeConfigChange(node.id, {
                ...config,
                connector: next,
                action: '',
                credential: '',
              })
            }
          />
        )
      case 'action_select': {
        const connectorDef = connectors.find((c) => c.type === config.connector)
        return (
          <Combobox
            value={String(value)}
            placeholder="— choose action —"
            emptyText="Choose an integration first"
            options={Object.entries(connectorDef?.actions ?? {}).map(([actionName, def]) => ({
              value: actionName,
              description: def.description,
            }))}
            onChange={(next) => setValue(field.name, next)}
          />
        )
      }
      case 'credential_select': {
        const matching = credentials.filter((c) => c.connector_type === config.connector)
        return (
          <select value={String(value)} onChange={(event) => setValue(field.name, event.target.value)}>
            <option value="">
              {matching.length ? '— choose credential —' : 'No credentials for this integration'}
            </option>
            {matching.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        )
      }
      case 'mcp_server_select':
        return (
          <select
            value={String(value)}
            onChange={(event) => {
              onNodeConfigChange(node.id, {
                ...config,
                server: event.target.value,
                tool: '',
              })
            }}
          >
            <option value="">
              {mcpServers.length ? '— choose MCP server —' : 'No MCP servers registered'}
            </option>
            {mcpServers.map((server) => (
              <option key={server.name} value={server.name}>
                {server.name}
              </option>
            ))}
          </select>
        )
      case 'mcp_tool_select':
        return (
          <McpToolSelect
            server={String(config.server ?? '')}
            value={String(value)}
            onChange={(tool) => setValue(field.name, tool)}
          />
        )
      case 'tool_select':
        return (
          <Combobox
            value={String(value)}
            placeholder="— choose a tool —"
            options={tools.map((tool) => ({
              value: tool.name,
              description: tool.description,
            }))}
            onChange={(next) => setValue(field.name, next)}
          />
        )
      case 'tool_multiselect': {
        const selected = Array.isArray(value) ? (value as string[]) : []
        return (
          <div className="tool-chips">
            {selected.length > 0 ? (
              <div className="chips">
                {selected.map((name) => (
                  <span className="chip" key={name}>
                    {name}
                    <button
                      type="button"
                      aria-label={`Remove ${name}`}
                      onClick={() =>
                        setValue(
                          field.name,
                          selected.filter((tool) => tool !== name),
                        )
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <div className="chips-empty">
                No tools yet — add one below, or attach tool nodes via the Tools port
              </div>
            )}
            <Combobox
              value=""
              placeholder="Add a tool…"
              options={tools
                .filter((tool) => !selected.includes(tool.name))
                .map((tool) => ({ value: tool.name, description: tool.description }))}
              onChange={(name) => setValue(field.name, [...selected, name])}
            />
          </div>
        )
      }
      case 'flow_select': {
        const pattern = flows.find((f) => f.id === value)
        return (
          <div>
            <select
              value={String(value)}
              onChange={(event) => {
                const next = flows.find((f) => f.id === event.target.value)
                // Reset params to the new pattern's defaults
                const params: Record<string, unknown> = {}
                next?.params.forEach((p) => {
                  if (p.default !== undefined) params[p.name] = p.default
                })
                onNodeConfigChange(node.id, { ...config, flow_type: event.target.value, params })
              }}
            >
              {flows.map((flow) => (
                <option key={flow.id} value={flow.id} title={flow.description}>
                  {flow.label}
                </option>
              ))}
            </select>
            {pattern && (
              <p className="config-subtitle flow-pattern-hint">
                {pattern.description} {pattern.order_hint}
              </p>
            )}
          </div>
        )
      }
      case 'agent_list':
        return (
          <FlowAgentListEditor
            agents={Array.isArray(value) ? (value as FlowAgentSpec[]) : []}
            models={models}
            minAgents={flows.find((f) => f.id === config.flow_type)?.min_agents ?? 1}
            onChange={(agents) => setValue(field.name, agents)}
          />
        )
      case 'json':
        return (
          <textarea
            defaultValue={JSON.stringify(value ?? {}, null, 2)}
            rows={5}
            spellCheck={false}
            onBlur={(event) => {
              try {
                setValue(field.name, JSON.parse(event.target.value || '{}'))
                event.target.classList.remove('invalid')
              } catch {
                event.target.classList.add('invalid')
              }
            }}
          />
        )
      default:
        return (
          <input
            value={String(value)}
            placeholder={field.placeholder}
            onChange={(event) => setValue(field.name, event.target.value)}
          />
        )
    }
  }

  return (
    <>
      <label className="field">
        <span>Label</span>
        <input
          value={node.data.label}
          onChange={(event) => onNodeConfigChange(node.id, config, event.target.value)}
        />
      </label>
      {def?.config_fields.filter(isTriggerFieldVisible).map((field) => (
        <label className="field" key={field.name}>
          <span>
            {field.name}
            {field.required ? ' *' : ''}
          </span>
          {renderField(field)}
        </label>
      ))}
      {node.data.nodeType === 'trigger' && (
        <>
          {config.trigger_kind === 'manual' && (
            <p className="config-subtitle">
              This workflow runs when you press ▶ Run — no automation is set up.
            </p>
          )}
          {config.trigger_kind !== 'manual' && (
            <label className="field field-checkbox">
              <input
                type="checkbox"
                checked={config.enabled !== false}
                onChange={(event) => setValue('enabled', event.target.checked)}
              />
              <span>Trigger enabled</span>
            </label>
          )}
          {(config.trigger_kind ?? 'schedule') === 'form' && onOpenIO && (
            <button
              className="form-editor-add"
              onClick={() => onOpenIO(node.id)}
              title="Open the full form editor (or double-click the node)"
            >
              ⛶ Open form editor
            </button>
          )}
          {(config.trigger_kind ?? 'schedule') === 'form' && (
            <FormFieldsEditor
              fields={(config.form_fields as FormField[] | undefined) ?? []}
              busy={false}
              onChange={(fields) => setValue('form_fields', fields)}
            />
          )}
          {(config.trigger_kind ?? 'schedule') === 'form' &&
            (automation?.form_enabled && automation.form_token ? (
              <TriggerUrl
                url={`${apiBase}/forms/${automation.form_token}`}
                hint="Share this URL — each submission runs the workflow with the field values as {{ input.<name> }}."
              />
            ) : (
              <div className="trigger-save-cta">
                <p className="config-subtitle">
                  Saving the workflow creates the hosted form and its shareable URL.
                </p>
                {onSaveWorkflow && (
                  <button className="primary" onClick={onSaveWorkflow}>
                    ✓ Save &amp; create form
                  </button>
                )}
              </div>
            ))}
          {config.trigger_kind === 'webhook' &&
            (automation?.webhook_enabled && automation.webhook_token ? (
              <TriggerUrl
                url={`${apiBase}/hooks/${automation.webhook_token}`}
                hint={
                  config.webhook_respond_mode === 'when_finished'
                    ? 'POST JSON here; the response waits for the run and returns its output.'
                    : 'POST JSON here; the body becomes the workflow input. Add ?wait=true to get the output back synchronously.'
                }
              />
            ) : (
              <div className="trigger-save-cta">
                <p className="config-subtitle">
                  Saving the workflow creates the webhook and its URL.
                </p>
                {onSaveWorkflow && (
                  <button className="primary" onClick={onSaveWorkflow}>
                    ✓ Save &amp; create webhook
                  </button>
                )}
              </div>
            ))}
        </>
      )}
      {node.data.nodeType === 'flow' && (
        <FlowParamsFields
          pattern={flows.find((f) => f.id === config.flow_type)}
          params={(config.params as Record<string, unknown> | undefined) ?? {}}
          onChange={(params) => setValue('params', params)}
        />
      )}
      {node.data.nodeType === 'tool' && config.tool_name ? (
        <ToolParamsHint tool={tools.find((t) => t.name === config.tool_name)} />
      ) : null}
      {node.data.nodeType === 'connector' && config.connector && config.action ? (
        <ConnectorParamsHint
          connector={connectors.find((c) => c.type === config.connector)}
          action={String(config.action)}
        />
      ) : null}
      {node.data.nodeType === 'mcp' && config.server && config.tool ? (
        <McpParamsHint server={String(config.server)} tool={String(config.tool)} />
      ) : null}
      {['tool', 'agent', 'connector', 'mcp', 'flow'].includes(node.data.nodeType) && (
        <p className="expression-hint">
          Tip: reference an upstream node's output with{' '}
          <code>{'{{ node_id.data.result }}'}</code>
        </p>
      )}
      {['agent', 'tool', 'connector', 'mcp'].includes(node.data.nodeType) && (
        <>
          <label className="field">
            <span>Run once per item (optional)</span>
            <input
              value={String(config.for_each ?? '')}
              placeholder="e.g. {{ fetch.data.items }}"
              spellCheck={false}
              onChange={(event) => setValue('for_each', event.target.value || undefined)}
            />
          </label>
          {Boolean(config.for_each) && (
            <>
              <p className="config-subtitle">
                This node runs once per list element — reference the current one with{' '}
                <code>{'{{ item }}'}</code> / <code>{'{{ item_index }}'}</code>. Results collect
                under <code>{`{{ ${node.id}.items }}`}</code>.
              </p>
              <label className="field">
                <span>Run in parallel (items at a time)</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={Number(config.for_each_concurrency ?? 1)}
                  onChange={(event) => {
                    const n = Math.max(1, Math.min(20, Number(event.target.value)))
                    setValue('for_each_concurrency', n > 1 ? n : undefined)
                  }}
                />
              </label>
              <p className="config-subtitle">
                1 = one at a time (sequential). Higher runs that many items concurrently —
                faster for lots of slow calls (APIs, LLMs).
              </p>
            </>
          )}
          <ExecutionPolicyFields
            policy={(config.execution as Record<string, unknown> | undefined) ?? {}}
            onChange={(policy) => setValue('execution', policy)}
          />
        </>
      )}
      {['agent', 'tool', 'connector', 'mcp', 'subworkflow', 'flow'].includes(
        node.data.nodeType,
      ) &&
        !hideNodeTest && <NodeTestSection workflowId={currentWorkflowId} nodeId={node.id} />}
    </>
  )
}

function FlowAgentListEditor({
  agents,
  models,
  minAgents,
  onChange,
}: {
  agents: FlowAgentSpec[]
  models: ModelOption[]
  minAgents: number
  onChange: (agents: FlowAgentSpec[]) => void
}) {
  const update = (index: number, patch: Partial<FlowAgentSpec>) => {
    onChange(agents.map((agent, i) => (i === index ? { ...agent, ...patch } : agent)))
  }
  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= agents.length) return
    const next = [...agents]
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }
  return (
    <div className="flow-agent-list">
      {agents.map((agent, index) => (
        <div className="flow-agent-card" key={index}>
          <div className="flow-agent-card-header">
            <span className="flow-agent-index">Agent {index + 1}</span>
            <span className="flow-agent-actions">
              <button title="Move up" disabled={index === 0} onClick={() => move(index, -1)}>
                ↑
              </button>
              <button
                title="Move down"
                disabled={index === agents.length - 1}
                onClick={() => move(index, 1)}
              >
                ↓
              </button>
              <button
                className="danger"
                title="Remove agent"
                onClick={() => onChange(agents.filter((_, i) => i !== index))}
              >
                ✕
              </button>
            </span>
          </div>
          <input
            value={agent.role ?? ''}
            placeholder="Role, e.g. Critic"
            onChange={(event) => update(index, { role: event.target.value })}
          />
          <textarea
            value={agent.goal ?? ''}
            placeholder="Goal"
            rows={2}
            onChange={(event) => update(index, { goal: event.target.value })}
          />
          <div className="flow-agent-row">
            <select
              value={agent.llm_model ?? models[0]?.id ?? ''}
              onChange={(event) => update(index, { llm_model: event.target.value })}
            >
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label}
                </option>
              ))}
            </select>
            <input
              type="number"
              value={agent.temperature ?? 0.7}
              min={0}
              max={1}
              step={0.1}
              title="Temperature"
              onChange={(event) => update(index, { temperature: Number(event.target.value) })}
            />
          </div>
        </div>
      ))}
      {agents.length < minAgents && (
        <p className="error-text">This pattern needs at least {minAgents} agents.</p>
      )}
      <button
        className="refresh-button"
        onClick={() =>
          onChange([...agents, { role: '', goal: '', llm_model: models[0]?.id, temperature: 0.7 }])
        }
      >
        + Add agent
      </button>
    </div>
  )
}

function FlowParamsFields({
  pattern,
  params,
  onChange,
}: {
  pattern: FlowPatternDef | undefined
  params: Record<string, unknown>
  onChange: (params: Record<string, unknown>) => void
}) {
  if (!pattern?.params.length) return null
  return (
    <details className="advanced-section" open>
      <summary>Pattern settings</summary>
      {pattern.params.map((spec) => (
        <label className="field" key={spec.name}>
          <span>{spec.name}</span>
          <input
            type="number"
            value={Number(params[spec.name] ?? spec.default ?? 0)}
            min={spec.min}
            max={spec.max}
            step={spec.max !== undefined && spec.max <= 1 ? 0.05 : 1}
            onChange={(event) => onChange({ ...params, [spec.name]: Number(event.target.value) })}
          />
        </label>
      ))}
    </details>
  )
}

function useMcpTools(server: string): { tools: McpToolInfo[]; error: string | null } {
  const [tools, setTools] = useState<McpToolInfo[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!server) {
      setTools([])
      return
    }
    let cancelled = false
    setError(null)
    listMcpServerTools(server)
      .then((result) => {
        if (!cancelled) setTools(result)
      })
      .catch((err) => {
        if (!cancelled) {
          setTools([])
          setError((err as Error).message)
        }
      })
    return () => {
      cancelled = true
    }
  }, [server])

  return { tools, error }
}

function McpToolSelect({
  server,
  value,
  onChange,
}: {
  server: string
  value: string
  onChange: (tool: string) => void
}) {
  const { tools, error } = useMcpTools(server)
  if (!server)
    return (
      <select disabled>
        <option>choose a server first</option>
      </select>
    )
  if (error) return <p className="error-text">Server unreachable: {error}</p>
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">— choose tool —</option>
      {tools.map((tool) => (
        <option key={tool.name} value={tool.name} title={tool.description}>
          {tool.name}
        </option>
      ))}
    </select>
  )
}

function McpParamsHint({ server, tool }: { server: string; tool: string }) {
  const { tools } = useMcpTools(server)
  const def = tools.find((t) => t.name === tool)
  if (!def) return null
  const required = new Set(def.input_schema.required ?? [])
  const properties = def.input_schema.properties ?? {}
  return (
    <div className="tool-params-hint">
      <span>
        {def.description || tool}. Params for <code>{tool}</code>:
      </span>
      <ul>
        {Object.entries(properties).map(([name, spec]) => (
          <li key={name}>
            <code>{name}</code>
            {required.has(name) ? ' (required)' : ''} — {spec.description ?? spec.type ?? ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

function ExecutionPolicyFields({
  policy,
  onChange,
}: {
  policy: Record<string, unknown>
  onChange: (policy: Record<string, unknown>) => void
}) {
  return (
    <details className="advanced-section">
      <summary>Advanced: retries & errors</summary>
      <label className="field">
        <span>Retry count</span>
        <input
          type="number"
          min={0}
          max={10}
          value={Number(policy.retry_count ?? 0)}
          onChange={(e) => onChange({ ...policy, retry_count: Number(e.target.value) })}
        />
      </label>
      <label className="field">
        <span>Timeout (seconds, 0 = none)</span>
        <input
          type="number"
          min={0}
          value={Number(policy.timeout_seconds ?? 0)}
          onChange={(e) => {
            const v = Number(e.target.value)
            const next = { ...policy }
            if (v > 0) next.timeout_seconds = v
            else delete next.timeout_seconds
            onChange(next)
          }}
        />
      </label>
      <label className="field field-checkbox">
        <input
          type="checkbox"
          checked={Boolean(policy.continue_on_error)}
          onChange={(e) => onChange({ ...policy, continue_on_error: e.target.checked })}
        />
        <span>Continue workflow if this node fails</span>
      </label>
    </details>
  )
}

function ConnectorParamsHint({
  connector,
  action,
}: {
  connector: ConnectorDef | undefined
  action: string
}) {
  const def = connector?.actions[action]
  if (!def) return null
  return (
    <div className="tool-params-hint">
      <span>
        {def.description}. Params for <code>{action}</code>:
      </span>
      <ul>
        {def.params.map((param) => (
          <li key={param.name}>
            <code>{param.name}</code>
            {param.required ? ' (required)' : ''}
            {param.example !== undefined ? ` — e.g. ${JSON.stringify(param.example)}` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

function ToolParamsHint({ tool }: { tool: ToolDef | undefined }) {
  if (!tool?.parameters?.properties) return null
  const required = new Set(tool.parameters.required ?? [])
  return (
    <div className="tool-params-hint">
      <span>Parameters for {tool.name}:</span>
      <ul>
        {Object.entries(tool.parameters.properties).map(([name, spec]) => (
          <li key={name}>
            <code>{name}</code>
            {required.has(name) ? ' (required)' : ''} — {spec.description ?? spec.type}
          </li>
        ))}
      </ul>
    </div>
  )
}

function TriggerUrl({ url, hint }: { url: string; hint: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="hook-url">
      <code>{url}</code>
      <button
        onClick={() => {
          navigator.clipboard.writeText(url)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? 'Copied!' : 'Copy'}
      </button>
      <p className="config-subtitle">{hint}</p>
    </div>
  )
}

export function NodeTestSection({
  workflowId,
  nodeId,
}: {
  workflowId: string | null
  nodeId: string
}) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<NodeTestResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Stale results from a previously selected node aren't meaningful
  useEffect(() => {
    setResult(null)
    setError(null)
  }, [nodeId])

  const run = async () => {
    if (!workflowId) return
    setBusy(true)
    setError(null)
    try {
      setResult(await testNode(workflowId, nodeId))
    } catch (err) {
      setResult(null)
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="node-test-result">
      <button onClick={run} disabled={busy || !workflowId} title={workflowId ? undefined : 'Save the workflow first'}>
        {busy ? 'Testing…' : '▶ Test this node'}
      </button>
      {!workflowId && <p className="config-subtitle">Save the workflow to test nodes.</p>}
      {error && <p className="error-text">{error}</p>}
      {result && (
        <>
          <p className="config-subtitle">
            {result.status === 'success' ? '✓ succeeded' : `✗ ${result.status}`}
            {result.upstream_from_run
              ? ` — upstream data from run ${result.upstream_from_run.slice(0, 8)}`
              : ' — no prior run; upstream references may not resolve'}
          </p>
          {result.error && <p className="error-text">{result.error}</p>}
          <pre>{JSON.stringify(result.output, null, 2)}</pre>
        </>
      )}
    </div>
  )
}
