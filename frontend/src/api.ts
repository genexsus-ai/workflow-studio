import type {
  Analysis,
  AnalysisCell,
  AnalysisSummary,
  AutomationConfig,
  DatasetAggregateEntry,
  DatasetAnalysis,
  DatasetRowsPage,
  DatasetSummary,
  FeedbackEntry,
  InsightsData,
  RunRecord,
  SourceColumn,
  SourceSummary,
  CredentialSummary,
  McpServerSummary,
  McpToolInfo,
  NodeTestResult,
  OAuthProvidersResponse,
  Palette,
  RunEvent,
  ValidationResult,
  WorkflowDoc,
  WorkflowSummary,
  WorkflowVersionInfo,
} from './types'

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'
const API = `${API_BASE}/api/v1`

// ---- API token (X-Studio-Token) ------------------------------------------
// Hosted backends require a token on every /api/v1 request. It is never
// baked into the bundle: the user is prompted on the first 401 and the
// value is kept in localStorage. Shadowing `fetch` at module scope routes
// every request in this file through the token-injecting wrapper.
const TOKEN_KEY = 'studio_api_token'

async function fetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers)
  const stored = localStorage.getItem(TOKEN_KEY)
  if (stored) headers.set('X-Studio-Token', stored)
  let response = await window.fetch(input, { ...init, headers })
  if (response.status === 401) {
    const entered = window.prompt(
      'This studio requires an API token (X-Studio-Token):',
    )
    if (entered && entered.trim()) {
      localStorage.setItem(TOKEN_KEY, entered.trim())
      headers.set('X-Studio-Token', entered.trim())
      response = await window.fetch(input, { ...init, headers })
    }
  }
  return response
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep statusText */
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const fetchPalette = () => fetch(`${API}/palette`).then((r) => json<Palette>(r))

export const submitFeedback = (payload: {
  message: string
  email?: string
  category?: string
  page?: string
}) =>
  fetch(`${API}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((r) => json<{ status: string; emailed: boolean }>(r))

export const listFeedback = () =>
  fetch(`${API}/feedback`).then((r) => json<FeedbackEntry[]>(r))

export const listWorkflows = () =>
  fetch(`${API}/workflows`).then((r) => json<WorkflowSummary[]>(r))

export const getWorkflow = (id: string) =>
  fetch(`${API}/workflows/${id}`).then((r) => json<WorkflowDoc>(r))

export const createWorkflow = (doc: WorkflowDoc) =>
  fetch(`${API}/workflows`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  }).then((r) => json<WorkflowDoc>(r))

// Imports a genxai workflow YAML (the CLI's `workflow run` format) as a new
// saved workflow.
export const importWorkflowYaml = (yamlText: string) =>
  fetch(`${API}/workflows/import-yaml`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml: yamlText }),
  }).then(async (r) => {
    if (!r.ok) {
      const body = await r.json().catch(() => ({}))
      throw new Error(body.detail ?? `Import failed (${r.status})`)
    }
    return json<WorkflowDoc>(r)
  })

export const updateWorkflow = (id: string, doc: WorkflowDoc) =>
  fetch(`${API}/workflows/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  }).then((r) => json<WorkflowDoc>(r))

export const deleteWorkflow = (id: string) =>
  fetch(`${API}/workflows/${id}`, { method: 'DELETE' })

export const validateWorkflow = (doc: WorkflowDoc) =>
  fetch(`${API}/workflows/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  }).then((r) => json<ValidationResult>(r))

/** Stream an ad-hoc run over SSE; invokes onEvent per parsed event. */
export async function streamRun(
  doc: WorkflowDoc,
  input: Record<string, unknown>,
  onEvent: (event: RunEvent) => void,
  modelOverride?: string,
): Promise<void> {
  const response = await fetch(`${API}/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workflow: doc, input, model_override: modelOverride || null }),
  })
  if (!response.ok || !response.body) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep statusText */
    }
    throw new Error(detail)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.trim()
      if (line.startsWith('data: ')) {
        onEvent(JSON.parse(line.slice(6)) as RunEvent)
      }
    }
  }
}

export interface GenerateProgressEvent {
  event: 'progress' | 'complete' | 'error'
  stage?: string
  message?: string
  [key: string]: unknown
}

export interface GenerateResult {
  workflow: WorkflowDoc
  open_questions: { question: string; default_assumption?: string }[]
  review: { approved: boolean; issues: string[] } | null
  warnings: string[]
  validation: ValidationResult
  generation_id: string | null
}

export async function generateWorkflow(
  prompt: string,
  crew: boolean,
  onEvent: (event: GenerateProgressEvent) => void,
  model?: string,
  currentWorkflow?: WorkflowDoc,
  name?: string,
): Promise<GenerateResult> {
  const response = await fetch(`${API}/workflows/generate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt,
      crew,
      ...(model ? { model } : {}),
      ...(name ? { name } : {}),
      ...(currentWorkflow ? { current_workflow: currentWorkflow } : {}),
    }),
  })
  if (!response.ok || !response.body) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep statusText */
    }
    throw new Error(detail)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: GenerateResult | null = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.trim()
      if (!line.startsWith('data: ')) continue
      const event = JSON.parse(line.slice(6)) as GenerateProgressEvent
      onEvent(event)
      if (event.event === 'complete') {
        result = event as unknown as GenerateResult
      } else if (event.event === 'error') {
        throw new Error(event.message ?? 'generation failed')
      }
    }
  }
  if (!result) throw new Error('generation stream ended without a result')
  return result
}

export const acceptGeneration = (generationId: string) =>
  fetch(`${API}/workflows/generate/${generationId}/accept`, { method: 'POST' })

export const updateAutomation = (id: string, config: AutomationConfig) =>
  fetch(`${API}/workflows/${id}/automation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  }).then((r) => json<WorkflowDoc>(r))

export const listRuns = () => fetch(`${API}/runs`).then((r) => json<RunRecord[]>(r))

export const retryRunFromFailure = (runId: string) =>
  fetch(`${API}/runs/${runId}/retry`, { method: 'POST' }).then((r) =>
    json<{ run_id: string }>(r),
  )

export const submitHumanInput = (runId: string, nodeId: string, response: unknown) =>
  fetch(`${API}/runs/${runId}/input`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, response }),
  }).then((r) => json<{ status: string }>(r))

export const testNode = (workflowId: string, nodeId: string, input?: Record<string, unknown>) =>
  fetch(`${API}/workflows/${workflowId}/test-node`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, input: input ?? {} }),
  }).then((r) => json<NodeTestResult>(r))

export const apiBase = API

export const listCredentials = () =>
  fetch(`${API}/credentials`).then((r) => json<CredentialSummary[]>(r))

export const listAnalyses = () =>
  fetch(`${API}/datascience/analyses`).then((r) => json<AnalysisSummary[]>(r))

export const createAnalysis = (name: string, sources: Record<string, string> = {}) =>
  fetch(`${API}/datascience/analyses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, sources }),
  }).then((r) => json<Analysis>(r))

export const getAnalysis = (id: string) =>
  fetch(`${API}/datascience/analyses/${id}`).then((r) => json<Analysis>(r))

export const updateAnalysis = (
  id: string,
  patch: { name?: string; sources?: Record<string, string> },
) =>
  fetch(`${API}/datascience/analyses/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).then((r) => json<Analysis>(r))

export const deleteAnalysis = (id: string) =>
  fetch(`${API}/datascience/analyses/${id}`, { method: 'DELETE' })

export const addAnalysisCell = (id: string, question: string) =>
  fetch(`${API}/datascience/analyses/${id}/cells`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }).then((r) => json<AnalysisCell>(r))

export const addManualCell = (id: string, sql: string, question?: string) =>
  fetch(`${API}/datascience/analyses/${id}/cells/manual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql, question: question ?? null }),
  }).then((r) => json<AnalysisCell>(r))

export const rerunAllCells = (id: string) =>
  fetch(`${API}/datascience/analyses/${id}/rerun`, { method: 'POST' }).then((r) =>
    json<Analysis>(r),
  )

export const updateAnalysisCell = (
  id: string,
  cellId: string,
  patch: { sql?: string; question?: string },
) =>
  fetch(`${API}/datascience/analyses/${id}/cells/${cellId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).then((r) => json<AnalysisCell>(r))

export const materializeCell = (id: string, cellId: string, dataset: string, mode: string) =>
  fetch(`${API}/datascience/analyses/${id}/cells/${cellId}/materialize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset, mode }),
  }).then((r) => json<{ dataset: string; written: number; total_rows: number }>(r))

export const rerunAnalysisCell = (id: string, cellId: string) =>
  fetch(`${API}/datascience/analyses/${id}/cells/${cellId}/rerun`, {
    method: 'POST',
  }).then((r) => json<AnalysisCell>(r))

export const deleteAnalysisCell = (id: string, cellId: string) =>
  fetch(`${API}/datascience/analyses/${id}/cells/${cellId}`, { method: 'DELETE' })

export const listSources = () =>
  fetch(`${API}/analytics/sources`).then((r) => json<SourceSummary[]>(r))

export const createSource = (name: string, kind: string, config: Record<string, unknown>) =>
  fetch(`${API}/analytics/sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, kind, config }),
  }).then((r) => json<SourceSummary>(r))

export const deleteSource = (id: string) =>
  fetch(`${API}/analytics/sources/${id}`, { method: 'DELETE' })

export const getSourceSchema = (id: string) =>
  fetch(`${API}/analytics/sources/${id}/schema`).then((r) => json<SourceColumn[]>(r))

export const getSourceRows = (id: string, limit = 50, offset = 0) =>
  fetch(`${API}/analytics/sources/${id}/rows?limit=${limit}&offset=${offset}`).then((r) =>
    json<DatasetRowsPage>(r),
  )

export const aggregateSource = (id: string, metric: string, field?: string, groupBy?: string) => {
  const params = new URLSearchParams({ metric })
  if (field) params.set('field', field)
  if (groupBy) params.set('group_by', groupBy)
  return fetch(`${API}/analytics/sources/${id}/aggregate?${params}`).then((r) =>
    json<DatasetAggregateEntry[]>(r),
  )
}

export interface SourceProfile {
  total_rows: number
  profiled_rows: number
  columns: {
    name: string
    type: string
    nulls: number
    distinct: number
    min?: number
    max?: number
    mean?: number
    std?: number
    top_values?: { value: string; count: number }[]
  }[]
}

export const getSourceProfile = (id: string) =>
  fetch(`${API}/analytics/sources/${id}/profile`).then((r) => json<SourceProfile>(r))

export const analyzeSource = (id: string, question?: string) =>
  fetch(`${API}/analytics/sources/${id}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: question || null }),
  }).then((r) => json<DatasetAnalysis>(r))

export interface DashboardReportSummary {
  id: string
  source: string
  focus?: string | null
  figures: number
  created_at: string
}

export interface DashboardReport {
  id: string
  source: string
  source_name: string
  focus?: string | null
  plan?: { name: string; kind: string; purpose: string; columns: string[] }[]
  report: string
  figures: { id: string; name: string }[]
  datasets: Record<string, number>
  metrics?: Record<string, unknown> | null
  stdout: string
  code: string
  review: { verdict: string; reason: string }[]
  created_at: string
}

export const createDashboardReport = (id: string, focus?: string) =>
  fetch(`${API}/analytics/sources/${id}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ focus: focus || null }),
  }).then((r) => json<DashboardReport>(r))

export const listDashboardReports = (source: string) =>
  fetch(`${API}/analytics/reports?source=${encodeURIComponent(source)}`).then((r) =>
    json<DashboardReportSummary[]>(r),
  )

export const getDashboardReport = (id: string) =>
  fetch(`${API}/analytics/reports/${id}`).then((r) => json<DashboardReport>(r))

export const deleteDashboardReport = (id: string) =>
  fetch(`${API}/analytics/reports/${id}`, { method: 'DELETE' })

export const materializeSource = (
  id: string,
  dataset: string,
  mode: string,
  intervalSeconds: number,
) =>
  fetch(`${API}/analytics/sources/${id}/materialize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset, mode, interval_seconds: intervalSeconds }),
  }).then((r) => json<{ workflow_id: string; workflow_name: string }>(r))

export const uploadFile = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return fetch(`${API}/files/upload`, { method: 'POST', body: form }).then((r) =>
    json<{ file: { id: string; name: string }; sheets: string[] | null }>(r),
  )
}

export const listCredentialTables = (credential: string) =>
  fetch(`${API}/analytics/credentials/${credential}/tables`).then((r) =>
    json<{ tables: string[] }>(r),
  )

export const listDatasets = () =>
  fetch(`${API}/datasets`).then((r) => json<DatasetSummary[]>(r))

export const getDatasetRows = (name: string, limit = 50, offset = 0) =>
  fetch(`${API}/datasets/${name}/rows?limit=${limit}&offset=${offset}`).then((r) =>
    json<DatasetRowsPage>(r),
  )

export const aggregateDataset = (
  name: string,
  metric: string,
  field?: string,
  groupBy?: string,
) => {
  const params = new URLSearchParams({ metric })
  if (field) params.set('field', field)
  if (groupBy) params.set('group_by', groupBy)
  return fetch(`${API}/datasets/${name}/aggregate?${params}`).then((r) =>
    json<DatasetAggregateEntry[]>(r),
  )
}

export const deleteDataset = (name: string) =>
  fetch(`${API}/datasets/${name}`, { method: 'DELETE' })

export const analyzeDataset = (name: string, question?: string) =>
  fetch(`${API}/datasets/${name}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: question || null }),
  }).then((r) => json<DatasetAnalysis>(r))

export const getInsights = (days: number) =>
  fetch(`${API}/insights?days=${days}`).then((r) => json<InsightsData>(r))

export const listWorkflowVersions = (id: string) =>
  fetch(`${API}/workflows/${id}/versions`).then((r) => json<WorkflowVersionInfo[]>(r))

export const restoreWorkflowVersion = (id: string, version: string) =>
  fetch(`${API}/workflows/${id}/versions/${version}/restore`, { method: 'POST' }).then(
    (r) => json<WorkflowDoc>(r),
  )

export const listOAuthProviders = () =>
  fetch(`${API}/oauth/providers`).then((r) => json<OAuthProvidersResponse>(r))

export const saveOAuthApp = (provider: string, clientId: string, clientSecret: string) =>
  fetch(`${API}/oauth/apps/${provider}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
  })

export const startOAuth = (provider: string, credentialName: string, scopes?: string[]) =>
  fetch(`${API}/oauth/${provider}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ credential_name: credentialName, scopes: scopes ?? null }),
  }).then((r) => json<{ authorize_url: string }>(r))

export const createCredential = (name: string, connectorType: string, config: Record<string, string>) =>
  fetch(`${API}/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, connector_type: connectorType, config }),
  }).then((r) => json<CredentialSummary>(r))

export const deleteCredential = (name: string) =>
  fetch(`${API}/credentials/${name}`, { method: 'DELETE' })

export const listMcpServers = () =>
  fetch(`${API}/mcp/servers`).then((r) => json<McpServerSummary[]>(r))

export const createMcpServer = (
  name: string,
  transport: string,
  config: Record<string, unknown>,
) =>
  fetch(`${API}/mcp/servers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, transport, config }),
  }).then((r) => json<{ name: string }>(r))

export const deleteMcpServer = (name: string) =>
  fetch(`${API}/mcp/servers/${name}`, { method: 'DELETE' })

export const listMcpServerTools = (name: string) =>
  fetch(`${API}/mcp/servers/${name}/tools`).then((r) => json<McpToolInfo[]>(r))

export const getRun = (runId: string) =>
  fetch(`${API}/runs/${runId}`).then((r) => json<RunRecord>(r))

export const cancelRun = (runId: string) =>
  fetch(`${API}/runs/${runId}/cancel`, { method: 'POST' }).then((r) => json<{ status: string }>(r))

export const rerunRun = (runId: string) =>
  fetch(`${API}/runs/${runId}/rerun`, { method: 'POST' }).then((r) =>
    json<{ run_id: string }>(r),
  )

export const deleteRun = (runId: string) =>
  fetch(`${API}/runs/${runId}`, { method: 'DELETE' })

export interface ModelInfo {
  id: string
  name: string
  model_type: string
  source_id: string
  target: string
  features: string[]
  metrics: Record<string, number>
  figures?: { id: string; name: string }[]
  created_at: string
}

export const listModels = () =>
  fetch(`${API}/datascience/models`).then((r) => json<ModelInfo[]>(r))

export const trainModel = (
  name: string,
  source: string,
  target: string,
  modelType: string,
) =>
  fetch(`${API}/datascience/models/train`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, source, target, model_type: modelType }),
  }).then((r) => json<ModelInfo>(r))

export const deleteModel = (id: string) =>
  fetch(`${API}/datascience/models/${id}`, { method: 'DELETE' })

export const scheduleReport = (
  analysisId: string,
  intervalSeconds: number,
  slackCredential?: string,
  slackChannel?: string,
) =>
  fetch(`${API}/datascience/analyses/${analysisId}/schedule-report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      interval_seconds: intervalSeconds,
      slack_credential: slackCredential || null,
      slack_channel: slackChannel || null,
    }),
  }).then((r) => json<{ workflow_id: string; workflow_name: string }>(r))

export interface ExperimentStage {
  name: string
  status: string
  artifact?: Record<string, unknown> | null
  verdicts: { verdict: string; reason: string }[]
  gate?: {
    question: string
    preview?: Record<string, unknown>
    approved?: boolean
    note?: string
  } | null
  error?: string | null
}

export interface ExperimentSummary {
  id: string
  objective: string
  source_id: string
  target?: string | null
  status: string
  error?: string | null
  stages_done: number
  stages_total: number
  created_at: string
  updated_at: string
}

export interface Experiment {
  id: string
  objective: string
  source_id: string
  target?: string | null
  status: string
  error?: string | null
  stages: ExperimentStage[]
  created_at: string
  updated_at: string
}

export const listExperiments = () =>
  fetch(`${API}/datascience/experiments`).then((r) => json<ExperimentSummary[]>(r))

export const createExperiment = (
  objective: string,
  source: string,
  target?: string,
  humanGates = false,
) =>
  fetch(`${API}/datascience/experiments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      objective,
      source,
      target: target || null,
      human_gates: humanGates,
    }),
  }).then((r) => json<Experiment>(r))

export const getExperiment = (id: string) =>
  fetch(`${API}/datascience/experiments/${id}`).then((r) => json<Experiment>(r))

export const rerunExperiment = (id: string) =>
  fetch(`${API}/datascience/experiments/${id}/rerun`, { method: 'POST' })

export const deleteExperiment = (id: string) =>
  fetch(`${API}/datascience/experiments/${id}`, { method: 'DELETE' })

export const resumeExperiment = (id: string, approve: boolean, note?: string) =>
  fetch(`${API}/datascience/experiments/${id}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approve, note: note || null }),
  }).then((r) => json<{ status: string }>(r))

export const compareExperiments = (id: string, otherId: string) =>
  fetch(`${API}/datascience/experiments/${id}/compare/${otherId}`).then((r) =>
    json<{ a: Record<string, unknown>; b: Record<string, unknown> }>(r),
  )
