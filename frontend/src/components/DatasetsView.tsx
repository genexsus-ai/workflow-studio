import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  aggregateSource,
  analyzeSource,
  createSource,
  deleteDataset,
  deleteSource,
  getSourceRows,
  getSourceSchema,
  listCredentialTables,
  listCredentials,
  listSources,
} from '../api'
import type {
  CredentialSummary,
  DatasetAggregateEntry,
  DatasetRowsPage,
  SourceColumn,
  SourceSummary,
} from '../types'

// Single hue for magnitude (validated on the app surface); the chart shows
// one measure, so this is sequential color, not identity.
const SEQUENTIAL_BLUE = '#2a78d6'
const PAGE_SIZE = 25

const KIND_META: Record<string, { icon: string; label: string }> = {
  dataset: { icon: '🗄', label: 'Datasets' },
  sql: { icon: '🐘', label: 'Databases' },
  file: { icon: '📄', label: 'Files' },
}

export function DatasetsView() {
  const [sources, setSources] = useState<SourceSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const refresh = useCallback(() => {
    listSources()
      .then((list) => {
        setSources(list)
        setSelected((current) =>
          current && list.some((s) => s.id === current)
            ? current
            : (list[0]?.id ?? null),
        )
      })
      .catch(() => setSources([]))
  }, [])

  useEffect(refresh, [refresh])

  const grouped = useMemo(() => {
    const groups: Record<string, SourceSummary[]> = {}
    for (const source of sources) {
      ;(groups[source.kind] ??= []).push(source)
    }
    return groups
  }, [sources])

  const selectedSource = sources.find((s) => s.id === selected) ?? null

  return (
    <div className="datasets">
      <aside className="datasets-list">
        <div className="datasets-list-header">
          <h1>Sources</h1>
          <span>
            <button onClick={() => setAdding(true)} title="Connect a data source">
              ＋
            </button>{' '}
            <button onClick={refresh} title="Refresh">
              ↻
            </button>
          </span>
        </div>
        {sources.length === 0 && (
          <p className="config-subtitle">
            No data yet. Add a <code>dataset_write</code> node to a workflow, or
            connect a database table with ＋.
          </p>
        )}
        {Object.entries(KIND_META).map(([kind, meta]) =>
          grouped[kind]?.length ? (
            <div key={kind}>
              <p className="source-group-label">
                {meta.icon} {meta.label}
              </p>
              {grouped[kind].map((source) => (
                <button
                  key={source.id}
                  className={`dataset-item${selected === source.id ? ' active' : ''}`}
                  onClick={() => setSelected(source.id)}
                >
                  <strong>{source.name}</strong>
                  <span>
                    {source.kind === 'sql'
                      ? `table ${String(source.config.table ?? '')}`
                      : `${source.rows ?? 0} rows${
                          source.last_written_at
                            ? ` · ${new Date(source.last_written_at).toLocaleDateString()}`
                            : ''
                        }`}
                  </span>
                </button>
              ))}
            </div>
          ) : null,
        )}
      </aside>
      {adding && (
        <AddSourceDialog
          onClose={() => setAdding(false)}
          onCreated={(id) => {
            setAdding(false)
            refresh()
            setSelected(id)
          }}
        />
      )}
      {selectedSource ? (
        <SourceDetail
          key={selectedSource.id}
          source={selectedSource}
          onDeleted={refresh}
        />
      ) : (
        <div className="dataset-detail" />
      )}
    </div>
  )
}

function AddSourceDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const [credentials, setCredentials] = useState<CredentialSummary[]>([])
  const [credential, setCredential] = useState('')
  const [tables, setTables] = useState<string[] | null>(null)
  const [table, setTable] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listCredentials()
      .then((all) => setCredentials(all.filter((c) => c.connector_type === 'postgres')))
      .catch(() => setCredentials([]))
  }, [])

  useEffect(() => {
    setTables(null)
    setTable('')
    if (!credential) return
    listCredentialTables(credential)
      .then((result) => {
        setTables(result.tables)
        setError(null)
      })
      .catch((err) => setError((err as Error).message))
  }, [credential])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await createSource(name || `${table} (${credential})`, 'sql', {
        credential,
        table,
      })
      onCreated(created.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="add-source-overlay" onClick={onClose}>
      <div className="add-source-dialog" onClick={(event) => event.stopPropagation()}>
        <h2>Connect a database table</h2>
        {credentials.length === 0 ? (
          <p className="config-subtitle">
            No database credentials yet — add a PostgreSQL credential in
            Workflow Studio's Credentials panel first.
          </p>
        ) : (
          <>
            <label className="field">
              <span>Credential</span>
              <select value={credential} onChange={(e) => setCredential(e.target.value)}>
                <option value="">Choose…</option>
                {credentials.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            {tables && (
              <label className="field">
                <span>Table</span>
                <select value={table} onChange={(e) => setTable(e.target.value)}>
                  <option value="">Choose…</option>
                  {tables.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {table && (
              <label className="field">
                <span>Source name</span>
                <input
                  value={name}
                  placeholder={`${table} (${credential})`}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
            )}
          </>
        )}
        {error && <p className="error-text">{error}</p>}
        <div className="credential-form-actions">
          <button
            className="primary-small"
            disabled={busy || !credential || !table}
            onClick={submit}
          >
            {busy ? 'Connecting…' : 'Connect'}
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

function SourceDetail({
  source,
  onDeleted,
}: {
  source: SourceSummary
  onDeleted: () => void
}) {
  const [page, setPage] = useState<DatasetRowsPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [schema, setSchema] = useState<SourceColumn[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getSourceSchema(source.id)
      .then(setSchema)
      .catch(() => setSchema([]))
  }, [source.id])

  useEffect(() => {
    getSourceRows(source.id, PAGE_SIZE, offset)
      .then((result) => {
        setPage(result)
        setError(null)
      })
      .catch((err) => {
        setPage(null)
        setError((err as Error).message)
      })
  }, [source.id, offset])

  const columns = useMemo(() => {
    if (schema.length > 0) return schema.map((c) => c.name).slice(0, 8)
    const keys = new Set<string>()
    for (const row of page?.rows ?? []) {
      for (const key of Object.keys(row)) {
        if (!key.startsWith('_')) keys.add(key)
      }
    }
    return [...keys].slice(0, 8)
  }, [schema, page])

  const numericColumns = useMemo(() => {
    if (schema.length > 0) {
      return schema
        .filter((c) => /int|float|num|real|double|decimal/.test(c.type))
        .map((c) => c.name)
    }
    return columns.filter((column) =>
      (page?.rows ?? []).some((row) => typeof row[column] === 'number'),
    )
  }, [schema, columns, page])

  const remove = async () => {
    if (source.kind === 'dataset') {
      await deleteDataset(String(source.config.dataset))
    } else {
      await deleteSource(source.id)
    }
    onDeleted()
  }

  return (
    <div className="dataset-detail">
      <div className="dataset-detail-header">
        <h2>{source.name}</h2>
        <span className="config-subtitle">
          {source.kind === 'sql'
            ? `${KIND_META.sql.icon} ${String(source.config.table ?? '')} · ${page?.total ?? '…'} rows`
            : `${page?.total ?? 0} rows`}
        </span>
        <button
          className="danger"
          title={source.kind === 'dataset' ? 'Delete this dataset' : 'Disconnect this source'}
          onClick={remove}
        >
          {source.kind === 'dataset' ? '🗑' : '✕'}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}

      <ChartBuilder sourceId={source.id} columns={columns} numericColumns={numericColumns} />
      <AnalyzeSection sourceId={source.id} />

      <section className="insights-card">
        <h2>Rows</h2>
        {!page || page.rows.length === 0 ? (
          <p className="config-subtitle">No rows.</p>
        ) : (
          <>
            <div className="dataset-table-wrap">
              <table className="insights-table">
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {page.rows.map((row, index) => (
                    <tr key={String(row._id ?? index)}>
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
            <div className="dataset-pager">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                ← Prev
              </button>
              <span className="config-subtitle">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}
              </span>
              <button
                disabled={offset + PAGE_SIZE >= page.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function ChartBuilder({
  sourceId,
  columns,
  numericColumns,
}: {
  sourceId: string
  columns: string[]
  numericColumns: string[]
}) {
  const [groupBy, setGroupBy] = useState('')
  const [metric, setMetric] = useState('count')
  const [field, setField] = useState('')
  const [entries, setEntries] = useState<DatasetAggregateEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const effectiveField = metric === 'count' ? undefined : field || numericColumns[0]
    if (metric !== 'count' && !effectiveField) {
      setEntries([])
      return
    }
    aggregateSource(sourceId, metric, effectiveField, groupBy || undefined)
      .then((result) => {
        setEntries(result.slice(0, 12))
        setError(null)
      })
      .catch((err) => setError((err as Error).message))
  }, [sourceId, metric, field, groupBy, numericColumns])

  const max = Math.max(1, ...entries.map((entry) => entry.value))

  return (
    <section className="insights-card">
      <h2>Chart</h2>
      <div className="insights-filters chart-builder-filters">
        <label>
          Group by{' '}
          <select value={groupBy} onChange={(event) => setGroupBy(event.target.value)}>
            <option value="">— whole source —</option>
            {columns.map((column) => (
              <option key={column} value={column}>
                {column}
              </option>
            ))}
          </select>
        </label>
        <label>
          Measure{' '}
          <select value={metric} onChange={(event) => setMetric(event.target.value)}>
            <option value="count">count of rows</option>
            {numericColumns.length > 0 && (
              <>
                <option value="sum">sum</option>
                <option value="avg">average</option>
                <option value="min">min</option>
                <option value="max">max</option>
              </>
            )}
          </select>
        </label>
        {metric !== 'count' && (
          <label>
            of{' '}
            <select
              value={field || numericColumns[0] || ''}
              onChange={(event) => setField(event.target.value)}
            >
              {numericColumns.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {error && <p className="error-text">{error}</p>}
      {entries.length === 0 ? (
        <p className="config-subtitle">Nothing to chart yet.</p>
      ) : (
        <div className="trigger-bars">
          {entries.map((entry) => (
            <div key={entry.group} className="trigger-bar-row" title={`${entry.rows} rows`}>
              <span className="trigger-bar-label truncate">{entry.group}</span>
              <div className="trigger-bar-track">
                <div
                  className="trigger-bar-fill"
                  style={{
                    width: `${Math.max((entry.value / max) * 100, 2)}%`,
                    background: SEQUENTIAL_BLUE,
                  }}
                />
              </div>
              <span className="trigger-bar-value">
                {Number.isInteger(entry.value) ? entry.value : entry.value.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function AnalyzeSection({ sourceId }: { sourceId: string }) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [insight, setInsight] = useState<string | null>(null)
  const [meta, setMeta] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await analyzeSource(sourceId, question || undefined)
      setInsight(result.insight)
      setMeta(`${result.model} · analyzed ${result.sampled_rows} of ${result.total_rows} rows`)
    } catch (err) {
      setError((err as Error).message)
      setInsight(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="insights-card">
      <h2>Ask AI</h2>
      <div className="insights-filters">
        <input
          className="analyze-question"
          value={question}
          placeholder="What patterns or outliers are in this data?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button disabled={busy} onClick={run}>
          {busy ? 'Analyzing…' : '✨ Analyze'}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
      {insight && (
        <div className="analyze-result">
          <pre>{insight}</pre>
          {meta && <p className="config-subtitle">{meta}</p>}
        </div>
      )}
    </section>
  )
}
