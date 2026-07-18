import type { ChangeEvent } from 'react'

import type { WorkflowSummary } from '../types'

interface ToolbarProps {
  workflowName: string
  workflows: WorkflowSummary[]
  currentId: string | null
  running: boolean
  dirty: boolean
  onNameChange: (name: string) => void
  onNew: () => void
  onSave: () => void
  onLoad: (id: string) => void
  onDelete: () => void
  onRun: () => void
  onImportYaml: (yamlText: string) => void
  onGenerate: () => void
  insightsOpen: boolean
  onToggleInsights: () => void
}

export function Toolbar({
  workflowName,
  workflows,
  currentId,
  running,
  dirty,
  onNameChange,
  onNew,
  onSave,
  onLoad,
  onDelete,
  onRun,
  onImportYaml,
  onGenerate,
  insightsOpen,
  onToggleInsights,
}: ToolbarProps) {
  const importFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    file.text().then(onImportYaml)
  }
  return (
    <header className="toolbar">
      <span className="brand">GenXAI Studio</span>
      <input
        className="workflow-name"
        value={workflowName}
        onChange={(event) => onNameChange(event.target.value)}
        placeholder="Workflow name"
      />
      {dirty && <span className="dirty-dot" title="Unsaved changes">●</span>}
      <div className="toolbar-actions">
        <select
          value={currentId ?? ''}
          onChange={(event) => event.target.value && onLoad(event.target.value)}
        >
          <option value="">Open workflow…</option>
          {workflows.map((workflow) => (
            <option key={workflow.id} value={workflow.id}>
              {workflow.name} ({workflow.node_count} nodes)
            </option>
          ))}
        </select>
        <button onClick={onNew}>New</button>
        <button onClick={onGenerate}>✨ Generate…</button>
        <button onClick={onSave}>Save</button>
        <label className="button-file">
          Import YAML…
          <input type="file" accept=".yaml,.yml" onChange={importFile} hidden />
        </label>
        {currentId && (
          <button className="danger" onClick={onDelete}>
            Delete
          </button>
        )}
        <button
          onClick={onToggleInsights}
          title="Run analytics for this studio"
          style={insightsOpen ? { fontWeight: 700 } : undefined}
        >
          📈 Insights
        </button>
        <button
          className="primary"
          onClick={onRun}
          disabled={running}
          title="Run the workflow now — automated triggers keep working independently"
        >
          {running ? 'Running…' : '▶ Run'}
        </button>
      </div>
    </header>
  )
}
