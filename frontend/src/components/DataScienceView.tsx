import { useCallback, useEffect, useState } from 'react'

import {
  addAnalysisCell,
  apiBase,
  getSourceRows,
  getSourceSchema,
  addManualCell,
  createAnalysis,
  createExperiment,
  deleteAnalysis,
  deleteAnalysisCell,
  deleteExperiment,
  deleteModel,
  getAnalysis,
  getExperiment,
  listAnalyses,
  listExperiments,
  listModels,
  listSources,
  materializeCell,
  rerunAllCells,
  rerunAnalysisCell,
  rerunExperiment,
  resumeExperiment,
  compareExperiments,
  scheduleReport,
  trainModel,
  updateAnalysis,
  updateAnalysisCell,
  type Experiment,
  type ExperimentStage,
  type ExperimentSummary,
  type ModelInfo,
} from '../api'
import type { Analysis, AnalysisCell, AnalysisSummary, SourceSummary } from '../types'

function sanitizeAlias(name: string): string {
  const alias = name.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  return /^[a-z_]/.test(alias) ? alias : `s_${alias}`
}

export function DataScienceView() {
  const [mode, setMode] = useState<'analyses' | 'experiments'>('analyses')
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  const refresh = useCallback(() => {
    listAnalyses()
      .then((list) => {
        setAnalyses(list)
        setSelected((current) =>
          current && list.some((a) => a.id === current) ? current : (list[0]?.id ?? null),
        )
      })
      .catch(() => setAnalyses([]))
  }, [])

  useEffect(refresh, [refresh])

  const create = async () => {
    if (!newName.trim()) return
    const analysis = await createAnalysis(newName.trim())
    setCreating(false)
    setNewName('')
    refresh()
    setSelected(analysis.id)
  }

  if (mode === 'experiments') {
    return <ExperimentsView onSwitchMode={() => setMode('analyses')} />
  }

  return (
    <div className="datasets">
      <aside className="datasets-list">
        <div className="add-source-tabs ds-mode-tabs">
          <button className="active">💬 Analyses</button>
          <button onClick={() => setMode('experiments')}>🧬 Experiments</button>
        </div>
        <div className="datasets-list-header">
          <h1>Analyses</h1>
          <span>
            <button onClick={() => setCreating(true)} title="New analysis">
              ＋
            </button>{' '}
            <button onClick={refresh} title="Refresh">
              ↻
            </button>
          </span>
        </div>
        {creating && (
          <div className="credential-form">
            <input
              value={newName}
              placeholder="Analysis name"
              autoFocus
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void create()}
            />
            <div className="credential-form-actions">
              <button className="primary-small" onClick={() => void create()}>
                Create
              </button>
              <button onClick={() => setCreating(false)}>Cancel</button>
            </div>
          </div>
        )}
        {analyses.length === 0 && !creating && (
          <p className="config-subtitle">
            No analyses yet. Create one, bind data sources, and ask questions —
            an agent writes and runs the SQL.
          </p>
        )}
        {analyses.map((analysis) => (
          <button
            key={analysis.id}
            className={`dataset-item${selected === analysis.id ? ' active' : ''}`}
            onClick={() => setSelected(analysis.id)}
          >
            <strong>{analysis.name}</strong>
            <span>
              {analysis.cell_count} cells · {new Date(analysis.updated_at).toLocaleDateString()}
            </span>
          </button>
        ))}
      </aside>
      {selected ? (
        <AnalysisDetail key={selected} analysisId={selected} onDeleted={refresh} />
      ) : (
        <div className="dataset-detail" />
      )}
      <aside className="models-rail">
        <ModelsPanel />
      </aside>
    </div>
  )
}

const STAGE_ICONS: Record<string, string> = {
  plan: '🗺',
  explore: '🔍',
  clean: '🧹',
  features: '🧱',
  model: '🤖',
  viz: '📈',
  report: '📋',
}

function ExperimentsView({ onSwitchMode }: { onSwitchMode: () => void }) {
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [objective, setObjective] = useState('')
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [humanGates, setHumanGates] = useState(false)
  const [sources, setSources] = useState<SourceSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    listExperiments()
      .then((list) => {
        setExperiments(list)
        setSelected((current) =>
          current && list.some((e) => e.id === current) ? current : (list[0]?.id ?? null),
        )
      })
      .catch(() => setExperiments([]))
  }, [])

  useEffect(refresh, [refresh])
  useEffect(() => {
    listSources().then(setSources).catch(() => setSources([]))
  }, [])

  const create = async () => {
    setError(null)
    try {
      const experiment = await createExperiment(
        objective.trim(),
        source,
        target.trim() || undefined,
        humanGates,
      )
      setCreating(false)
      setObjective('')
      setTarget('')
      refresh()
      setSelected(experiment.id)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="datasets">
      <aside className="datasets-list">
        <div className="add-source-tabs ds-mode-tabs">
          <button onClick={onSwitchMode}>💬 Analyses</button>
          <button className="active">🧬 Experiments</button>
        </div>
        <div className="datasets-list-header">
          <h1>Experiments</h1>
          <span>
            <button onClick={() => setCreating(true)} title="New experiment">
              ＋
            </button>{' '}
            <button onClick={refresh} title="Refresh">
              ↻
            </button>
          </span>
        </div>
        {creating && (
          <div className="credential-form">
            <textarea
              rows={2}
              value={objective}
              placeholder="Objective, e.g. Predict churn from customer data"
              onChange={(e) => setObjective(e.target.value)}
            />
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">Source…</option>
              {sources
                .filter((s) => s.kind !== 'duckdb')
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
            </select>
            <input
              value={target}
              placeholder="Target column (optional)"
              onChange={(e) => setTarget(e.target.value)}
            />
            <label className="field field-checkbox">
              <input
                type="checkbox"
                checked={humanGates}
                onChange={(e) => setHumanGates(e.target.checked)}
              />
              <span>Pause for my approval at key steps</span>
            </label>
            <div className="credential-form-actions">
              <button
                className="primary-small"
                disabled={!objective.trim() || !source}
                onClick={() => void create()}
              >
                Run crew
              </button>
              <button onClick={() => setCreating(false)}>Cancel</button>
            </div>
            {error && <p className="error-text">{error}</p>}
          </div>
        )}
        {experiments.length === 0 && !creating && (
          <p className="config-subtitle">
            No experiments yet. State an objective — a crew of agents plans,
            explores, and cleans the data, with every artifact reviewed.
          </p>
        )}
        {experiments.map((experiment) => (
          <button
            key={experiment.id}
            className={`dataset-item${selected === experiment.id ? ' active' : ''}`}
            onClick={() => setSelected(experiment.id)}
          >
            <strong className="truncate">{experiment.objective}</strong>
            <span>
              {experiment.status === 'running'
                ? `running · ${experiment.stages_done}/${experiment.stages_total}`
                : experiment.status}
            </span>
          </button>
        ))}
      </aside>
      {selected ? (
        <ExperimentDetail key={selected} experimentId={selected} onDeleted={refresh} />
      ) : (
        <div className="dataset-detail" />
      )}
    </div>
  )
}

function ExperimentDetail({
  experimentId,
  onDeleted,
}: {
  experimentId: string
  onDeleted: () => void
}) {
  const [experiment, setExperiment] = useState<Experiment | null>(null)

  const load = useCallback(() => {
    getExperiment(experimentId).then(setExperiment).catch(() => setExperiment(null))
  }, [experimentId])

  useEffect(load, [load])

  // Poll while the crew is working
  useEffect(() => {
    if (!experiment || !['queued', 'running'].includes(experiment.status)) return
    const timer = setInterval(load, 1500)
    return () => clearInterval(timer)
  }, [experiment, load])

  if (!experiment) return <div className="dataset-detail" />

  return (
    <div className="dataset-detail">
      <div className="dataset-detail-header">
        <h2 className="truncate">{experiment.objective}</h2>
        <span className={`node-output-status ${
          experiment.status === 'ok'
            ? 'status-completed'
            : experiment.status === 'error'
              ? 'status-failed'
              : 'status-running-chip'
        }`}>
          {experiment.status}
        </span>
        <span className="credential-row-actions">
          <a
            className="export-link"
            href={`${apiBase}/datascience/experiments/${experimentId}/export`}
            title="Download as a runnable Python project"
          >
            ⬇ Project
          </a>
          <button
            title="Re-run the crew against current data"
            onClick={async () => {
              await rerunExperiment(experimentId)
              load()
            }}
          >
            ↻
          </button>
          <button
            className="danger"
            title="Delete experiment"
            onClick={async () => {
              await deleteExperiment(experimentId)
              onDeleted()
            }}
          >
            🗑
          </button>
        </span>
      </div>
      {experiment.error && <p className="error-text">{experiment.error}</p>}

      <details className="insights-card">
        <summary className="profile-summary">
          Source preview <code>{experiment.source_id}</code>
        </summary>
        <SourcePreview sourceId={experiment.source_id} />
      </details>

      {experiment.status === 'waiting' && (
        <GateBanner experiment={experiment} onDecided={load} />
      )}

      {experiment.stages.map((stage) => (
        <StageCard key={stage.name} stage={stage} />
      ))}

      <CompareSection experimentId={experimentId} />
    </div>
  )
}

function GateBanner({
  experiment,
  onDecided,
}: {
  experiment: Experiment
  onDecided: () => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const stage = experiment.stages.find((s) => s.gate && s.gate.approved === undefined)
  if (!stage?.gate) return null

  const decide = async (approve: boolean) => {
    setBusy(true)
    try {
      await resumeExperiment(experiment.id, approve, note || undefined)
      onDecided()
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="insights-card gate-banner">
      <h2>⏸ Waiting for you</h2>
      <p className="cell-narrative">{stage.gate.question}</p>
      {stage.gate.preview && (
        <pre className="cell-sql">{JSON.stringify(stage.gate.preview, null, 1)}</pre>
      )}
      <div className="insights-filters">
        <input
          className="analyze-question"
          value={note}
          placeholder="Optional note for the crew"
          onChange={(e) => setNote(e.target.value)}
        />
        <button className="primary-small" disabled={busy} onClick={() => void decide(true)}>
          ✓ Approve
        </button>
        <button className="danger" disabled={busy} onClick={() => void decide(false)}>
          ✕ Reject
        </button>
      </div>
    </section>
  )
}

function CompareSection({ experimentId }: { experimentId: string }) {
  const [others, setOthers] = useState<ExperimentSummary[]>([])
  const [comparison, setComparison] = useState<{
    a: Record<string, unknown>
    b: Record<string, unknown>
  } | null>(null)

  useEffect(() => {
    listExperiments()
      .then((list) => setOthers(list.filter((e) => e.id !== experimentId)))
      .catch(() => setOthers([]))
  }, [experimentId])

  if (others.length === 0) return null

  const FIELDS: [string, string][] = [
    ['status', 'Status'],
    ['cleaned_rows', 'Cleaned rows'],
    ['feature_columns', 'Feature columns'],
    ['model_type', 'Model'],
    ['cv_mean', 'CV mean'],
    ['recommendation', 'Recommendation'],
  ]

  return (
    <section className="insights-card">
      <h2>Compare</h2>
      <select
        value=""
        onChange={(e) => {
          if (e.target.value) {
            void compareExperiments(experimentId, e.target.value).then(setComparison)
          }
        }}
      >
        <option value="">Compare with…</option>
        {others.map((other) => (
          <option key={other.id} value={other.id}>
            {other.objective}
          </option>
        ))}
      </select>
      {comparison && (
        <table className="insights-table" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th />
              <th className="truncate">{String(comparison.a.objective)}</th>
              <th className="truncate">{String(comparison.b.objective)}</th>
            </tr>
          </thead>
          <tbody>
            {FIELDS.map(([key, label]) => (
              <tr key={key}>
                <td>{label}</td>
                <td>{comparison.a[key] == null ? '—' : String(comparison.a[key])}</td>
                <td>{comparison.b[key] == null ? '—' : String(comparison.b[key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function StageCard({ stage }: { stage: ExperimentStage }) {
  const artifact = stage.artifact ?? {}
  return (
    <section className={`insights-card cell-card${stage.status === 'error' ? ' cell-error' : ''}`}>
      <div className="cell-header">
        <strong>
          {STAGE_ICONS[stage.name] ?? '⚙'} {stage.name}
        </strong>
        <span className={`node-output-status ${
          stage.status === 'ok'
            ? 'status-completed'
            : stage.status === 'error'
              ? 'status-failed'
              : stage.status === 'running'
                ? 'status-running-chip'
                : 'status-skipped'
        }`}>
          {stage.status}
        </span>
      </div>

      {stage.name === 'plan' && artifact && stage.status === 'ok' && (
        <p className="cell-narrative">
          <strong>{String(artifact.task_type ?? '')}</strong>
          {artifact.target ? <> · target <code>{String(artifact.target)}</code></> : null}
          <br />
          Explore: {String(artifact.exploration_focus ?? '—')}
          <br />
          Clean: {String(artifact.cleaning_focus ?? '—')}
        </p>
      )}

      {stage.name === 'explore' &&
        Array.isArray((artifact as { queries?: unknown }).queries) &&
        ((artifact as { queries: Record<string, unknown>[] }).queries).map(
          (query, index) => (
            <div key={index} className="explore-query">
              <p className="config-subtitle">{String(query.purpose ?? '')}</p>
              {query.status === 'ok' ? (
                <pre className="cell-sql">
                  {JSON.stringify((query.rows as unknown[])?.slice(0, 5) ?? [], null, 1)}
                </pre>
              ) : (
                <p className="error-text">{String(query.error ?? '')}</p>
              )}
              <details className="output-paths">
                <summary>SQL</summary>
                <pre className="cell-sql">{String(query.sql ?? '')}</pre>
              </details>
            </div>
          ),
        )}

      {stage.name === 'clean' && stage.status === 'ok' && (
        <>
          <p className="cell-narrative">{String(artifact.intent ?? '')}</p>
          <p className="config-subtitle">
            → dataset <code>{String(artifact.dataset ?? '')}</code> ·{' '}
            {String(artifact.row_count ?? '?')} rows — now available in Analytics
            and as a catalog source.
          </p>
          <details className="output-paths">
            <summary>SQL</summary>
            <pre className="cell-sql">{String(artifact.sql ?? '')}</pre>
          </details>
        </>
      )}

      {stage.name === 'features' && stage.status === 'ok' && (
        <>
          <p className="cell-narrative">{String(artifact.intent ?? '')}</p>
          <p className="config-subtitle">
            → dataset <code>{String(artifact.dataset ?? '')}</code> ·{' '}
            {String(artifact.row_count ?? '?')} rows ·{' '}
            {(artifact.columns as string[] | undefined)?.length ?? '?'} columns
          </p>
          <details className="output-paths">
            <summary>SQL</summary>
            <pre className="cell-sql">{String(artifact.sql ?? '')}</pre>
          </details>
        </>
      )}

      {stage.name === 'model' && stage.status === 'ok' && (
        artifact.skipped ? (
          <p className="config-subtitle">{String(artifact.reason ?? 'Skipped')}</p>
        ) : (
          <>
            <p className="cell-narrative">
              <strong>{String(artifact.model_type ?? artifact.approach ?? '')}</strong>
              {artifact.rationale ? ` — ${String(artifact.rationale)}` : ''}
            </p>
            {Boolean(artifact.cross_validation) && (
              <p className="config-subtitle">
                CV ({String((artifact.cross_validation as Record<string, unknown>).folds)}-fold{' '}
                {String((artifact.cross_validation as Record<string, unknown>).metric)}):{' '}
                {String((artifact.cross_validation as Record<string, unknown>).mean)} ±{' '}
                {String((artifact.cross_validation as Record<string, unknown>).std)}
                {(artifact.cross_validation as Record<string, unknown>).overfit_warning
                  ? ' · ⚠ possible overfit'
                  : ''}
              </p>
            )}
            {Boolean(artifact.holdout_metrics ?? artifact.metrics) && (
              <pre className="cell-sql">
                {JSON.stringify(artifact.holdout_metrics ?? artifact.metrics, null, 1)}
              </pre>
            )}
            {Boolean(artifact.predictions_dataset) && (
              <p className="config-subtitle">
                Predictions → dataset <code>{String(artifact.predictions_dataset)}</code>
                {' · model '}<code>{String(artifact.model_name ?? '')}</code> in the Models rail
              </p>
            )}
            {Boolean(artifact.code) && (
              <details className="output-paths">
                <summary>Model code</summary>
                <pre className="cell-sql">{String(artifact.code)}</pre>
              </details>
            )}
          </>
        )
      )}

      {stage.name === 'viz' && stage.status === 'ok' && (
        <>
          <div className="figure-row">
            {((artifact.figures as { id: string; name: string }[] | undefined) ?? []).map(
              (figure) => (
                <a
                  key={figure.id}
                  href={`${apiBase}/files/${figure.id}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img src={`${apiBase}/files/${figure.id}`} alt={figure.name} />
                </a>
              ),
            )}
          </div>
          <details className="output-paths">
            <summary>Visualization code</summary>
            <pre className="cell-sql">{String(artifact.code ?? '')}</pre>
          </details>
        </>
      )}

      {stage.name === 'report' && stage.status === 'ok' && (
        <>
          <p className="config-subtitle">
            Recommendation:{' '}
            <strong>
              {artifact.recommendation === 'ship'
                ? '🚀 ship'
                : artifact.recommendation === 'abandon'
                  ? '🛑 abandon'
                  : '🔁 iterate'}
            </strong>
          </p>
          <pre className="analyze-result-pre">{String(artifact.report ?? '')}</pre>
        </>
      )}

      {stage.error && stage.status === 'error' && (
        <p className="error-text">{stage.error}</p>
      )}

      {stage.gate && stage.gate.approved !== undefined && (
        <p className="config-subtitle">
          {stage.gate.approved ? '✅ you approved' : '⛔ you rejected'}
          {stage.gate.note ? ` — ${stage.gate.note}` : ''}
        </p>
      )}

      {stage.verdicts.length > 0 && (
        <p className="config-subtitle">
          {stage.verdicts.map((verdict, index) => (
            <span key={index}>
              {verdict.verdict === 'approve' ? '✅' : '♻️'} reviewer: {verdict.reason}{' '}
            </span>
          ))}
        </p>
      )}
    </section>
  )
}

function SourcePreview({ sourceId }: { sourceId: string }) {
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null)
  const [total, setTotal] = useState(0)
  const [schema, setSchema] = useState<{ name: string; type: string }[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getSourceRows(sourceId, 8, 0), getSourceSchema(sourceId)])
      .then(([page, columns]) => {
        if (cancelled) return
        setRows(page.rows)
        setTotal(page.total)
        setSchema(columns)
        setError(null)
      })
      .catch((err) => !cancelled && setError((err as Error).message))
    return () => {
      cancelled = true
    }
  }, [sourceId])

  if (error) return <p className="error-text">{error}</p>
  if (!rows) return <p className="config-subtitle">Loading preview…</p>
  if (rows.length === 0) return <p className="config-subtitle">Source is empty.</p>

  const columns =
    schema.length > 0
      ? schema.map((c) => c.name).slice(0, 8)
      : Object.keys(rows[0]).filter((k) => !k.startsWith('_')).slice(0, 8)
  const typeOf = (name: string) => schema.find((c) => c.name === name)?.type

  return (
    <div className="source-preview">
      <div className="dataset-table-wrap">
        <table className="insights-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} title={typeOf(column)}>
                  {column}
                  {typeOf(column) ? (
                    <span className="preview-type"> {typeOf(column)}</span>
                  ) : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column} className="truncate">
                    {row[column] == null
                      ? '—'
                      : typeof row[column] === 'object'
                        ? JSON.stringify(row[column])
                        : String(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="config-subtitle">
        Showing {rows.length} of {total} rows · full explorer in the Analytics app
      </p>
    </div>
  )
}

function ModelsPanel() {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [sources, setSources] = useState<SourceSummary[]>([])
  const [training, setTraining] = useState(false)
  const [name, setName] = useState('')
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [modelType, setModelType] = useState('linear_regression')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    listModels().then(setModels).catch(() => setModels([]))
    listSources().then(setSources).catch(() => setSources([]))
  }, [])

  useEffect(refresh, [refresh])

  const metricLabel = (model: ModelInfo) =>
    model.metrics.r2 != null
      ? `R² ${model.metrics.r2}`
      : model.metrics.accuracy != null
        ? `acc ${model.metrics.accuracy}`
        : ''

  return (
    <div>
      <div className="datasets-list-header">
        <h1>Models</h1>
        <button onClick={() => setTraining((open) => !open)} title="Train a model">
          ＋
        </button>
      </div>
      {training && (
        <div className="credential-form">
          <input value={name} placeholder="Model name" onChange={(e) => setName(e.target.value)} />
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">Source…</option>
            {sources
              .filter((s) => s.kind !== 'duckdb')
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
          </select>
          <input
            value={target}
            placeholder="Target column"
            onChange={(e) => setTarget(e.target.value)}
          />
          <select value={modelType} onChange={(e) => setModelType(e.target.value)}>
            <option value="linear_regression">Linear regression</option>
            <option value="logistic_regression">Logistic regression</option>
            <option value="random_forest_regression">Random forest (reg)</option>
            <option value="random_forest_classification">Random forest (clf)</option>
          </select>
          <div className="credential-form-actions">
            <button
              className="primary-small"
              disabled={busy || !name.trim() || !source || !target.trim()}
              onClick={async () => {
                setBusy(true)
                setError(null)
                try {
                  await trainModel(name.trim(), source, target.trim(), modelType)
                  setTraining(false)
                  setName('')
                  setTarget('')
                  refresh()
                } catch (err) {
                  setError((err as Error).message)
                } finally {
                  setBusy(false)
                }
              }}
            >
              {busy ? 'Training…' : 'Train'}
            </button>
            <button onClick={() => setTraining(false)}>Cancel</button>
          </div>
          {error && <p className="error-text">{error}</p>}
        </div>
      )}
      {models.length === 0 && !training && (
        <p className="config-subtitle">
          No models yet. Train one on a catalog source; apply it with the
          <code> model_predict</code> tool in workflows.
        </p>
      )}
      {models.map((model) => (
        <div key={model.id} className="credential-row">
          <span title={`target: ${model.target} · features: ${model.features.join(', ')}`}>
            <strong>{model.name}</strong>{' '}
            <em>
              {model.model_type.replace(/_/g, ' ')} · {metricLabel(model)}
            </em>
          </span>
          <button
            className="danger"
            onClick={async () => {
              await deleteModel(model.id)
              refresh()
            }}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

function AnalysisDetail({
  analysisId,
  onDeleted,
}: {
  analysisId: string
  onDeleted: () => void
}) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [sources, setSources] = useState<SourceSummary[]>([])
  const [question, setQuestion] = useState('')
  const [manualMode, setManualMode] = useState(false)
  const [manualSql, setManualSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [scheduling, setScheduling] = useState(false)
  const [reportInterval, setReportInterval] = useState(86400)
  const [slackCredential, setSlackCredential] = useState('')
  const [slackChannel, setSlackChannel] = useState('')
  const [scheduled, setScheduled] = useState<string | null>(null)
  const [previewSource, setPreviewSource] = useState<string | null>(null)

  const load = useCallback(() => {
    getAnalysis(analysisId).then(setAnalysis).catch(() => setAnalysis(null))
  }, [analysisId])

  useEffect(load, [load])
  useEffect(() => {
    listSources().then(setSources).catch(() => setSources([]))
  }, [])

  if (!analysis) return <div className="dataset-detail" />

  const boundIds = new Set(Object.values(analysis.sources))
  const unbound = sources.filter((s) => !boundIds.has(s.id) && s.kind !== 'duckdb')

  const bindSource = async (source: SourceSummary) => {
    const alias = sanitizeAlias(source.name)
    const next = { ...analysis.sources, [alias]: source.id }
    setAnalysis(await updateAnalysis(analysisId, { sources: next }))
  }

  const unbindAlias = async (alias: string) => {
    const next = { ...analysis.sources }
    delete next[alias]
    setAnalysis(await updateAnalysis(analysisId, { sources: next }))
  }

  const ask = async () => {
    setBusy(true)
    setError(null)
    try {
      if (manualMode) {
        await addManualCell(analysisId, manualSql, question || undefined)
        setManualSql('')
      } else {
        await addAnalysisCell(analysisId, question)
      }
      setQuestion('')
      load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dataset-detail">
      <div className="dataset-detail-header">
        <h2>{analysis.name}</h2>
        <span className="credential-row-actions">
          {analysis.cells.length > 0 && (
            <>
              <button
                disabled={busy}
                title="Re-run every cell against current data"
                onClick={async () => {
                  setBusy(true)
                  try {
                    setAnalysis(await rerunAllCells(analysisId))
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                ↻ Rerun all
              </button>
              <button
                disabled={busy}
                title="Create a scheduled workflow that reruns this analysis and reports the findings"
                onClick={() => setScheduling((open) => !open)}
              >
                📅
              </button>
            </>
          )}
          <button
            className="danger"
            title="Delete this analysis"
            onClick={async () => {
              await deleteAnalysis(analysisId)
              onDeleted()
            }}
          >
            🗑
          </button>
        </span>
      </div>

      {scheduling && (
        <section className="insights-card">
          <h2>Schedule report</h2>
          {scheduled ? (
            <p className="config-subtitle">✓ {scheduled}</p>
          ) : (
            <>
              <div className="insights-filters chart-builder-filters">
                <label>
                  Every{' '}
                  <select
                    value={reportInterval}
                    onChange={(e) => setReportInterval(Number(e.target.value))}
                  >
                    <option value={3600}>hour</option>
                    <option value={86400}>day</option>
                    <option value={604800}>week</option>
                  </select>
                </label>
                <label>
                  Slack credential{' '}
                  <input
                    value={slackCredential}
                    placeholder="optional"
                    onChange={(e) => setSlackCredential(e.target.value)}
                  />
                </label>
                <label>
                  Channel{' '}
                  <input
                    value={slackChannel}
                    placeholder="#reports"
                    onChange={(e) => setSlackChannel(e.target.value)}
                  />
                </label>
                <button
                  className="primary-small"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      const result = await scheduleReport(
                        analysisId,
                        reportInterval,
                        slackCredential || undefined,
                        slackChannel || undefined,
                      )
                      setScheduled(
                        `Workflow "${result.workflow_name}" created and scheduled.`,
                      )
                    } catch (err) {
                      setError((err as Error).message)
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  Create
                </button>
              </div>
              <p className="config-subtitle">
                Generates a workflow that reruns every cell and reports the
                findings — open it in Workflow Studio to customize.
              </p>
            </>
          )}
        </section>
      )}

      <section className="insights-card">
        <h2>Data</h2>
        <div className="source-chips">
          {Object.entries(analysis.sources).map(([alias, sourceId]) => {
            const source = sources.find((s) => s.id === sourceId)
            const active = previewSource === sourceId
            return (
              <span
                key={alias}
                className={`source-chip${active ? ' source-chip-active' : ''}`}
                title={`${sourceId} — click to preview`}
              >
                <button
                  className="source-chip-name"
                  onClick={() => setPreviewSource(active ? null : sourceId)}
                >
                  <code>{alias}</code> {source ? `· ${source.name}` : ''} 👁
                </button>
                <button onClick={() => void unbindAlias(alias)}>✕</button>
              </span>
            )
          })}
          {unbound.length > 0 && (
            <select
              value=""
              onChange={(e) => {
                const source = sources.find((s) => s.id === e.target.value)
                if (source) void bindSource(source)
              }}
            >
              <option value="">+ Bind source…</option>
              {unbound.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.kind})
                </option>
              ))}
            </select>
          )}
        </div>
        {Object.keys(analysis.sources).length === 0 && (
          <p className="config-subtitle">
            Bind at least one source — the agent queries them as SQL tables by alias.
          </p>
        )}
        {previewSource && <SourcePreview key={previewSource} sourceId={previewSource} />}
      </section>

      {analysis.cells.map((cell) => (
        <CellCard
          key={cell.id}
          analysisId={analysisId}
          cell={cell}
          onChanged={load}
        />
      ))}

      <section className="insights-card">
        <h2>{manualMode ? 'Run SQL' : 'Ask about this data'}</h2>
        <div className="insights-filters">
          <input
            className="analyze-question"
            value={question}
            placeholder={
              manualMode ? 'Optional label for this query' : 'e.g. Which region grew fastest?'
            }
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !manualMode && !busy && void ask()}
          />
          <button
            disabled={busy || (manualMode ? !manualSql.trim() : !question.trim())}
            className="primary-small"
            onClick={() => void ask()}
          >
            {busy ? 'Working…' : manualMode ? '▶ Run' : '✨ Ask'}
          </button>
        </div>
        {manualMode && (
          <textarea
            className="cell-sql-input"
            rows={3}
            value={manualSql}
            spellCheck={false}
            placeholder="SELECT … FROM <alias> …"
            onChange={(e) => setManualSql(e.target.value)}
          />
        )}
        <label className="field field-checkbox">
          <input
            type="checkbox"
            checked={manualMode}
            onChange={(e) => setManualMode(e.target.checked)}
          />
          <span>Write SQL myself</span>
        </label>
        {error && <p className="error-text">{error}</p>}
      </section>
    </div>
  )
}

// Sequential blue for single-measure charts (validated on the app surface)
const CHART_BLUE = '#2a78d6'

function CellChart({ cell }: { cell: AnalysisCell }) {
  const chart = cell.chart
  const rows = cell.result_rows ?? []
  if (!chart || rows.length === 0) return null
  const points = rows
    .map((row) => ({ x: String(row[chart.x] ?? '—'), y: Number(row[chart.y]) }))
    .filter((p) => Number.isFinite(p.y))
    .slice(0, 30)
  if (points.length === 0) return null

  if (chart.type === 'bar') {
    const max = Math.max(1, ...points.map((p) => Math.abs(p.y)))
    return (
      <div className="trigger-bars cell-chart">
        {points.slice(0, 12).map((p) => (
          <div key={p.x} className="trigger-bar-row">
            <span className="trigger-bar-label truncate">{p.x}</span>
            <div className="trigger-bar-track">
              <div
                className="trigger-bar-fill"
                style={{
                  width: `${Math.max((Math.abs(p.y) / max) * 100, 2)}%`,
                  background: CHART_BLUE,
                }}
              />
            </div>
            <span className="trigger-bar-value">
              {Number.isInteger(p.y) ? p.y : p.y.toFixed(2)}
            </span>
          </div>
        ))}
      </div>
    )
  }

  // line: values in row order over an ordered x column
  const width = 560
  const height = 120
  const values = points.map((p) => p.y)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const step = width / Math.max(points.length - 1, 1)
  const coords = points.map((p, i) => ({
    cx: i * step,
    cy: 8 + (1 - (p.y - min) / span) * (height - 30),
    label: p.x,
  }))
  return (
    <div className="cell-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${chart.y} over ${chart.x}`}>
        <line x1={0} y1={height - 16} x2={width} y2={height - 16} stroke="#e2e8f0" />
        <polyline
          fill="none"
          stroke={CHART_BLUE}
          strokeWidth={2}
          points={coords.map((c) => `${c.cx},${c.cy}`).join(' ')}
        />
        {coords.map((c, i) => (
          <g key={i}>
            <circle cx={c.cx} cy={c.cy} r={3} fill={CHART_BLUE} />
            {(points.length <= 8 || i % Math.ceil(points.length / 8) === 0) && (
              <text x={c.cx} y={height - 4} textAnchor="middle" className="chart-tick">
                {c.label.slice(0, 10)}
              </text>
            )}
          </g>
        ))}
      </svg>
      <p className="config-subtitle">
        {cell.chart?.y} by {cell.chart?.x}
      </p>
    </div>
  )
}

function CellCard({
  analysisId,
  cell,
  onChanged,
}: {
  analysisId: string
  cell: AnalysisCell
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [sqlDraft, setSqlDraft] = useState(cell.sql ?? '')
  const [saving, setSaving] = useState<string | null>(null)
  const [datasetName, setDatasetName] = useState('')
  const [savingToDataset, setSavingToDataset] = useState(false)

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className={`insights-card cell-card${cell.status === 'error' ? ' cell-error' : ''}`}>
      <div className="cell-header">
        <strong>{cell.question ?? 'SQL query'}</strong>
        <span className="credential-row-actions">
          <button
            disabled={busy}
            title="Re-run against current data"
            onClick={() => void act(() => rerunAnalysisCell(analysisId, cell.id))}
          >
            ↻
          </button>
          <button
            disabled={busy}
            title="Save this result as a dataset"
            onClick={() => setSavingToDataset((open) => !open)}
          >
            →🗄
          </button>
          <button
            disabled={busy}
            title="Edit the SQL"
            onClick={() => {
              setSqlDraft(cell.sql ?? '')
              setEditing((open) => !open)
            }}
          >
            ✎
          </button>
          <button
            className="danger"
            disabled={busy}
            title="Delete cell"
            onClick={() => void act(() => deleteAnalysisCell(analysisId, cell.id))}
          >
            ✕
          </button>
        </span>
      </div>

      {savingToDataset && (
        <div className="insights-filters">
          <input
            className="analyze-question"
            value={datasetName}
            placeholder="dataset name, e.g. revenue_by_region"
            onChange={(e) => setDatasetName(e.target.value)}
          />
          <button
            className="primary-small"
            disabled={busy || !datasetName.trim()}
            onClick={() =>
              void act(async () => {
                const result = await materializeCell(
                  analysisId,
                  cell.id,
                  datasetName.trim(),
                  'replace',
                )
                setSaving(`Saved ${result.written} rows to dataset "${result.dataset}"`)
                setSavingToDataset(false)
              })
            }
          >
            Save
          </button>
        </div>
      )}
      {saving && <p className="config-subtitle">✓ {saving}</p>}

      {editing && (
        <div>
          <textarea
            className="cell-sql-input"
            rows={3}
            value={sqlDraft}
            spellCheck={false}
            onChange={(e) => setSqlDraft(e.target.value)}
          />
          <div className="credential-form-actions">
            <button
              className="primary-small"
              disabled={busy || !sqlDraft.trim()}
              onClick={() =>
                void act(async () => {
                  await updateAnalysisCell(analysisId, cell.id, { sql: sqlDraft })
                  setEditing(false)
                })
              }
            >
              Save & run
            </button>
            <button onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      )}

      {cell.status === 'error' ? (
        <p className="error-text">{cell.error}</p>
      ) : (
        <>
          <CellChart cell={cell} />
          {cell.result_rows && cell.result_rows.length > 0 && (
            <div className="dataset-table-wrap">
              <table className="insights-table">
                <thead>
                  <tr>
                    {(cell.columns ?? []).map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {cell.result_rows.slice(0, 15).map((row, index) => (
                    <tr key={index}>
                      {(cell.columns ?? []).map((column) => (
                        <td key={column} className="truncate">
                          {row[column] == null ? '—' : String(row[column])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {cell.row_count != null && cell.row_count > 15 && (
            <p className="config-subtitle">Showing 15 of {cell.row_count} rows.</p>
          )}
          {cell.narrative && <p className="cell-narrative">{cell.narrative}</p>}
        </>
      )}

      {cell.sql && !editing && (
        <details className="output-paths">
          <summary>SQL</summary>
          <pre className="cell-sql">{cell.sql}</pre>
        </details>
      )}
    </section>
  )
}
