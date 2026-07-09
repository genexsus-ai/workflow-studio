export interface Position {
  x: number
  y: number
}

export interface NodeDoc {
  id: string
  type: string
  label?: string | null
  position: Position
  config: Record<string, unknown>
}

export interface EdgeDoc {
  id?: string | null
  source: string
  target: string
  condition?: string | null
  parallel?: boolean
  attach?: string | null
}

export interface WorkflowDoc {
  id?: string | null
  name: string
  description?: string
  nodes: NodeDoc[]
  edges: EdgeDoc[]
  automation?: AutomationConfig
  shared_memory?: boolean
}

export interface WorkflowSummary {
  id: string
  name: string
  description?: string
  node_count: number
}

export interface ConfigField {
  name: string
  type: string
  required?: boolean
  default?: unknown
  placeholder?: string
  min?: number
  max?: number
  options?: string[]
}

export interface NodeTypeDef {
  type: string
  label: string
  description: string
  color: string
  attachment?: string
  config_fields: ConfigField[]
}

export interface ToolDef {
  name: string
  description: string
  category?: string | null
  parameters: {
    type?: string
    properties?: Record<string, { type?: string; description?: string; default?: unknown }>
    required?: string[]
  }
}

export interface ModelOption {
  id: string
  label: string
  provider: string
}

export interface FlowParamSpec {
  name: string
  type: string
  default?: number
  min?: number
  max?: number
}

export interface FlowPatternDef {
  id: string
  label: string
  description: string
  order_hint: string
  min_agents: number
  params: FlowParamSpec[]
}

export interface FlowAgentSpec {
  role: string
  goal: string
  backstory?: string
  llm_model?: string
  temperature?: number
}

export interface Palette {
  node_types: NodeTypeDef[]
  tools: ToolDef[]
  models: ModelOption[]
  connectors: ConnectorDef[]
  flows?: FlowPatternDef[]
}

export type NodeRunStatus = 'idle' | 'running' | 'completed' | 'failed' | 'skipped'

export interface NodeResult {
  output: unknown
  status: string
  duration_ms?: number
  error?: string
}

export interface RunEvent {
  event: 'started' | 'node' | 'complete' | 'error'
  data: Record<string, unknown>
}

export interface ValidationIssue {
  level: 'error' | 'warning'
  message: string
  node_id?: string | null
}

export interface ValidationResult {
  valid: boolean
  issues: ValidationIssue[]
}

export interface AutomationConfig {
  webhook_enabled: boolean
  webhook_token?: string | null
  webhook_provider?: string
  webhook_secret?: string | null
  webhook_event_filter?: string | null
  schedule_enabled: boolean
  interval_seconds: number
}

export interface RunRecord {
  run_id: string
  workflow: string
  status: string
  started_at?: string | null
  completed_at?: string | null
  metadata?: { trigger?: string }
  error?: string | null
  result?: {
    nodes_executed?: number
    node_results?: Record<string, { output: unknown; status: string; duration_ms?: number; error?: string }>
  } | null
}

export interface ConnectorParamSpec {
  name: string
  required?: boolean
  example?: unknown
}

export interface ConnectorActionDef {
  description: string
  params: ConnectorParamSpec[]
}

export interface ConnectorDef {
  type: string
  label: string
  color: string
  icon?: string
  credential_fields: { name: string; secret?: boolean; example?: string }[]
  actions: Record<string, ConnectorActionDef>
}

export interface CredentialSummary {
  name: string
  connector_type: string
}

export interface McpServerSummary {
  name: string
  transport: string
  target: string
}

export interface McpToolInfo {
  name: string
  description: string
  input_schema: {
    properties?: Record<string, { type?: string; description?: string }>
    required?: string[]
  }
}
