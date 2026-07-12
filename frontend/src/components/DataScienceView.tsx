import { useCallback, useEffect, useState } from 'react'

import {
  addAnalysisCell,
  addManualCell,
  createAnalysis,
  deleteAnalysis,
  deleteAnalysisCell,
  getAnalysis,
  listAnalyses,
  listSources,
  rerunAnalysisCell,
  updateAnalysis,
} from '../api'
import type { Analysis, AnalysisCell, AnalysisSummary, SourceSummary } from '../types'

function sanitizeAlias(name: string): string {
  const alias = name.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  return /^[a-z_]/.test(alias) ? alias : `s_${alias}`
}

export function DataScienceView() {
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

  return (
    <div className="datasets">
      <aside className="datasets-list">
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
      </div>

      <section className="insights-card">
        <h2>Data</h2>
        <div className="source-chips">
          {Object.entries(analysis.sources).map(([alias, sourceId]) => {
            const source = sources.find((s) => s.id === sourceId)
            return (
              <span key={alias} className="source-chip" title={sourceId}>
                <code>{alias}</code> {source ? `· ${source.name}` : ''}
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
            className="danger"
            disabled={busy}
            title="Delete cell"
            onClick={() => void act(() => deleteAnalysisCell(analysisId, cell.id))}
          >
            ✕
          </button>
        </span>
      </div>

      {cell.status === 'error' ? (
        <p className="error-text">{cell.error}</p>
      ) : (
        <>
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

      {cell.sql && (
        <details className="output-paths">
          <summary>SQL</summary>
          <pre className="cell-sql">{cell.sql}</pre>
        </details>
      )}
    </section>
  )
}
