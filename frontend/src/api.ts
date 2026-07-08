import type {
  AutomationConfig,
  RunRecord,
  CredentialSummary,
  McpServerSummary,
  McpToolInfo,
  Palette,
  RunEvent,
  ValidationResult,
  WorkflowDoc,
  WorkflowSummary,
} from './types'

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'
const API = `${API_BASE}/api/v1`

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

export const apiBase = API

export const listCredentials = () =>
  fetch(`${API}/credentials`).then((r) => json<CredentialSummary[]>(r))

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
