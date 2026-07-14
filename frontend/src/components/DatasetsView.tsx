import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  aggregateSource,
  analyzeSource,
  apiBase,
  createSource,
  deleteDataset,
  deleteSource,
  getSourceProfile,
  getSourceRows,
  getSourceSchema,
  listCredentialTables,
  listCredentials,
  listSources,
  materializeSource,
  uploadFile,
  type SourceProfile,
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
  gsheet: { icon: '📗', label: 'Google Sheets' },
  s3: { icon: '🪣', label: 'S3' },
  duckdb: { icon: '🦆', label: 'Federated' },
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
                      ? source.config.table
                        ? `table ${String(source.config.table)}`
                        : 'custom query'
                      : source.kind === 'file'
                        ? String(source.config.file_name ?? source.config.format ?? 'file')
                        : source.kind === 'gsheet'
                          ? String(source.config.range ?? 'sheet')
                          : source.kind === 's3'
                            ? `s3://${String(source.config.bucket ?? '')}/${String(source.config.key ?? '')}`
                            : source.kind === 'duckdb'
                              ? 'federated query'
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
          existingSources={sources}
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

function sanitizeAlias(name: string): string {
  const alias = name.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  return /^[a-z_]/.test(alias) ? alias : `s_${alias}`
}

function AddSourceDialog({
  existingSources,
  onClose,
  onCreated,
}: {
  existingSources: SourceSummary[]
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const [mode, setMode] = useState<'sql' | 'file' | 'gsheet' | 'duckdb' | 's3'>('sql')
  const [sqlMode, setSqlMode] = useState<'table' | 'custom'>('table')
  const [customSql, setCustomSql] = useState('')
  const [credentials, setCredentials] = useState<CredentialSummary[]>([])
  const [credential, setCredential] = useState('')
  const [tables, setTables] = useState<string[] | null>(null)
  const [table, setTable] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uploaded, setUploaded] = useState<{
    id: string
    name: string
    sheets: string[] | null
  } | null>(null)
  const [sheet, setSheet] = useState('')
  const [gsheetCredential, setGsheetCredential] = useState('')
  const [spreadsheetId, setSpreadsheetId] = useState('')
  const [sheetRange, setSheetRange] = useState('Sheet1!A1:Z1000')
  const [federatedSql, setFederatedSql] = useState('')
  const [federatedPicks, setFederatedPicks] = useState<Record<string, string>>({})
  const [googleCredentials, setGoogleCredentials] = useState<CredentialSummary[]>([])
  const [s3Credentials, setS3Credentials] = useState<CredentialSummary[]>([])
  const [s3Credential, setS3Credential] = useState('')
  const [s3Bucket, setS3Bucket] = useState('')
  const [s3Key, setS3Key] = useState('')

  useEffect(() => {
    listCredentials()
      .then((all) => {
        setGoogleCredentials(all.filter((c) => c.connector_type === 'google_workspace'))
        setS3Credentials(all.filter((c) => c.connector_type === 's3'))
      })
      .catch(() => {
        setGoogleCredentials([])
        setS3Credentials([])
      })
  }, [])

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

  const submitSql = async () => {
    setBusy(true)
    setError(null)
    try {
      const config: Record<string, unknown> = { credential }
      let fallbackName: string
      if (sqlMode === 'custom') {
        config.sql = customSql
        fallbackName = `Query (${credential})`
      } else {
        config.table = table
        fallbackName = `${table} (${credential})`
      }
      const created = await createSource(name || fallbackName, 'sql', config)
      onCreated(created.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onFilePicked = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const result = await uploadFile(file)
      setUploaded({ id: result.file.id, name: result.file.name, sheets: result.sheets })
      setSheet(result.sheets?.[0] ?? '')
      if (!name) setName(result.file.name.replace(/\.(xlsx|csv)$/i, ''))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submitS3 = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await createSource(name || s3Key.split('/').pop() || 'S3 object', 's3', {
        credential: s3Credential,
        bucket: s3Bucket,
        key: s3Key,
      })
      onCreated(created.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submitGsheet = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await createSource(name || `Sheet ${spreadsheetId.slice(0, 8)}`, 'gsheet', {
        credential: gsheetCredential,
        spreadsheet_id: spreadsheetId,
        range: sheetRange,
      })
      onCreated(created.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submitFederated = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await createSource(name || 'Federated query', 'duckdb', {
        sql: federatedSql,
        sources: federatedPicks,
      })
      onCreated(created.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submitFile = async () => {
    if (!uploaded) return
    setBusy(true)
    setError(null)
    try {
      const format = uploaded.name.toLowerCase().endsWith('.csv') ? 'csv' : 'xlsx'
      const config: Record<string, unknown> = {
        file_id: uploaded.id,
        format,
        file_name: uploaded.name,
      }
      if (format === 'xlsx' && sheet) config.sheet = sheet
      const created = await createSource(name || uploaded.name, 'file', config)
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
        <h2>Add a data source</h2>
        <div className="add-source-tabs">
          <button className={mode === 'sql' ? 'active' : ''} onClick={() => setMode('sql')}>
            🐘 Database
          </button>
          <button className={mode === 'file' ? 'active' : ''} onClick={() => setMode('file')}>
            📄 File
          </button>
          <button className={mode === 'gsheet' ? 'active' : ''} onClick={() => setMode('gsheet')}>
            📗 Sheet
          </button>
          <button className={mode === 's3' ? 'active' : ''} onClick={() => setMode('s3')}>
            🪣 S3
          </button>
          <button className={mode === 'duckdb' ? 'active' : ''} onClick={() => setMode('duckdb')}>
            🦆 Federated
          </button>
        </div>

        {mode === 'sql' &&
          (credentials.length === 0 ? (
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
              {credential && (
                <label className="field field-checkbox">
                  <input
                    type="checkbox"
                    checked={sqlMode === 'custom'}
                    onChange={(e) => setSqlMode(e.target.checked ? 'custom' : 'table')}
                  />
                  <span>Custom SQL query (read-only)</span>
                </label>
              )}
              {sqlMode === 'custom' && credential && (
                <label className="field">
                  <span>SELECT query</span>
                  <textarea
                    rows={4}
                    value={customSql}
                    spellCheck={false}
                    placeholder="SELECT region, SUM(total) AS revenue FROM orders GROUP BY region"
                    onChange={(e) => setCustomSql(e.target.value)}
                  />
                </label>
              )}
              {sqlMode === 'table' && tables && (
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
              {(table || (sqlMode === 'custom' && customSql)) && (
                <label className="field">
                  <span>Source name</span>
                  <input
                    value={name}
                    placeholder={sqlMode === 'custom' ? `Query (${credential})` : `${table} (${credential})`}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
              )}
            </>
          ))}

        {mode === 'file' && (
          <>
            <label className="field">
              <span>Excel (.xlsx) or CSV file</span>
              <input
                type="file"
                accept=".xlsx,.csv"
                disabled={busy}
                onChange={(e) => void onFilePicked(e.target.files?.[0])}
              />
            </label>
            {uploaded && (
              <>
                <p className="config-subtitle">📎 {uploaded.name} uploaded</p>
                {uploaded.sheets && uploaded.sheets.length > 1 && (
                  <label className="field">
                    <span>Sheet</span>
                    <select value={sheet} onChange={(e) => setSheet(e.target.value)}>
                      {uploaded.sheets.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="field">
                  <span>Source name</span>
                  <input
                    value={name}
                    placeholder={uploaded.name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
              </>
            )}
          </>
        )}

        {mode === 'gsheet' &&
          (googleCredentials.length === 0 ? (
            <p className="config-subtitle">
              No Google credentials yet — connect a Google account in the
              Credentials panel first.
            </p>
          ) : (
            <>
              <label className="field">
                <span>Credential</span>
                <select
                  value={gsheetCredential}
                  onChange={(e) => setGsheetCredential(e.target.value)}
                >
                  <option value="">Choose…</option>
                  {googleCredentials.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Spreadsheet ID (from the sheet URL)</span>
                <input
                  value={spreadsheetId}
                  placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
                  onChange={(e) => setSpreadsheetId(e.target.value.trim())}
                />
              </label>
              <label className="field">
                <span>Range</span>
                <input
                  value={sheetRange}
                  onChange={(e) => setSheetRange(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Source name</span>
                <input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
            </>
          ))}

        {mode === 's3' &&
          (s3Credentials.length === 0 ? (
            <p className="config-subtitle">
              No S3 credentials yet — add an AWS S3 credential in the
              Credentials panel first.
            </p>
          ) : (
            <>
              <label className="field">
                <span>Credential</span>
                <select value={s3Credential} onChange={(e) => setS3Credential(e.target.value)}>
                  <option value="">Choose…</option>
                  {s3Credentials.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Bucket</span>
                <input value={s3Bucket} onChange={(e) => setS3Bucket(e.target.value.trim())} />
              </label>
              <label className="field">
                <span>Object key (.csv or .xlsx)</span>
                <input
                  value={s3Key}
                  placeholder="reports/q2.xlsx"
                  onChange={(e) => setS3Key(e.target.value.trim())}
                />
              </label>
              <label className="field">
                <span>Source name</span>
                <input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
            </>
          ))}

        {mode === 'duckdb' && (
          <>
            <p className="config-subtitle">
              Tick sources to load as tables, then join them with DuckDB SQL.
            </p>
            {existingSources
              .filter((s) => s.kind !== 'duckdb')
              .slice(0, 12)
              .map((s) => {
                const alias = sanitizeAlias(s.name)
                const picked = Object.entries(federatedPicks).some(([, id]) => id === s.id)
                return (
                  <label key={s.id} className="field field-checkbox">
                    <input
                      type="checkbox"
                      checked={picked}
                      onChange={(e) => {
                        const next = { ...federatedPicks }
                        if (e.target.checked) next[alias] = s.id
                        else {
                          for (const [key, value] of Object.entries(next)) {
                            if (value === s.id) delete next[key]
                          }
                        }
                        setFederatedPicks(next)
                      }}
                    />
                    <span>
                      {s.name} → <code>{alias}</code>
                    </span>
                  </label>
                )
              })}
            <label className="field">
              <span>DuckDB SQL (joins & window functions welcome)</span>
              <textarea
                rows={4}
                value={federatedSql}
                spellCheck={false}
                placeholder="SELECT a.region, SUM(b.total) FROM sheet_data a JOIN orders b USING (region) GROUP BY 1"
                onChange={(e) => setFederatedSql(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Source name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          </>
        )}

        {error && <p className="error-text">{error}</p>}
        <div className="credential-form-actions">
          {mode === 's3' ? (
            <button
              className="primary-small"
              disabled={busy || !s3Credential || !s3Bucket || !s3Key}
              onClick={submitS3}
            >
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          ) : mode === 'gsheet' ? (
            <button
              className="primary-small"
              disabled={busy || !gsheetCredential || !spreadsheetId || !sheetRange}
              onClick={submitGsheet}
            >
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          ) : mode === 'duckdb' ? (
            <button
              className="primary-small"
              disabled={busy || !federatedSql.trim()}
              onClick={submitFederated}
            >
              {busy ? 'Running…' : 'Create'}
            </button>
          ) : mode === 'sql' ? (
            <button
              className="primary-small"
              disabled={
                busy ||
                !credential ||
                (sqlMode === 'table' ? !table : !customSql.trim())
              }
              onClick={submitSql}
            >
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          ) : (
            <button
              className="primary-small"
              disabled={busy || !uploaded}
              onClick={submitFile}
            >
              {busy ? 'Working…' : 'Add source'}
            </button>
          )}
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
        <span className="credential-row-actions">
          <a
            className="export-link"
            href={`${apiBase}/analytics/sources/${source.id}/export?format=csv`}
            title="Download all rows as CSV"
          >
            ⬇ CSV
          </a>
          <a
            className="export-link"
            href={`${apiBase}/analytics/sources/${source.id}/export?format=parquet`}
            title="Download all rows as Parquet"
          >
            ⬇ Parquet
          </a>
          <button
            className="danger"
            title={source.kind === 'dataset' ? 'Delete this dataset' : 'Disconnect this source'}
            onClick={remove}
          >
            {source.kind === 'dataset' ? '🗑' : '✕'}
          </button>
        </span>
      </div>
      {error && <p className="error-text">{error}</p>}

      <ProfileSection sourceId={source.id} />
      <ChartBuilder sourceId={source.id} columns={columns} numericColumns={numericColumns} />
      {source.kind === 'sql' && <MaterializeSection source={source} />}
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

function ProfileSection({ sourceId }: { sourceId: string }) {
  const [profile, setProfile] = useState<SourceProfile | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    if (profile) return
    getSourceProfile(sourceId)
      .then(setProfile)
      .catch((err) => setError((err as Error).message))
  }

  const fmt = (value: number | undefined) =>
    value == null ? '—' : Number.isInteger(value) ? String(value) : value.toFixed(2)

  return (
    <details className="insights-card" onToggle={(e) => (e.target as HTMLDetailsElement).open && load()}>
      <summary className="profile-summary">Profile</summary>
      {error && <p className="error-text">{error}</p>}
      {profile && (
        <>
          <p className="config-subtitle">
            {profile.profiled_rows} of {profile.total_rows} rows profiled
          </p>
          <div className="dataset-table-wrap">
            <table className="insights-table">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th className="num">Nulls</th>
                  <th className="num">Distinct</th>
                  <th className="num">Min</th>
                  <th className="num">Mean</th>
                  <th className="num">Max</th>
                  <th>Top values</th>
                </tr>
              </thead>
              <tbody>
                {profile.columns.map((column) => (
                  <tr key={column.name}>
                    <td>
                      <code>{column.name}</code>
                    </td>
                    <td>{column.type}</td>
                    <td className="num">{column.nulls}</td>
                    <td className="num">{column.distinct}</td>
                    <td className="num">{fmt(column.min)}</td>
                    <td className="num">{fmt(column.mean)}</td>
                    <td className="num">{fmt(column.max)}</td>
                    <td className="truncate">
                      {column.top_values
                        ?.map((entry) => `${entry.value} (${entry.count})`)
                        .join(', ') ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </details>
  )
}

function MaterializeSection({ source }: { source: SourceSummary }) {
  const [open, setOpen] = useState(false)
  const [dataset, setDataset] = useState('')
  const [mode, setMode] = useState('replace')
  const [interval, setInterval] = useState(3600)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await materializeSource(source.id, dataset, mode, interval)
      setResult(
        `Workflow "${created.workflow_name}" created and scheduled — the dataset will appear after its first run.`,
      )
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="insights-card">
      <h2>Sync to dataset</h2>
      {!open ? (
        <>
          <p className="config-subtitle">
            Generate a scheduled workflow that copies this source into a durable
            dataset — history, transforms, and agents can build on it.
          </p>
          <button onClick={() => setOpen(true)}>⟳ Set up sync…</button>
        </>
      ) : result ? (
        <p className="config-subtitle">✓ {result}</p>
      ) : (
        <>
          <div className="insights-filters chart-builder-filters">
            <label>
              Dataset{' '}
              <input
                value={dataset}
                placeholder="e.g. orders_snapshot"
                onChange={(e) => setDataset(e.target.value)}
              />
            </label>
            <label>
              Mode{' '}
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="replace">replace (mirror)</option>
                <option value="append">append (history)</option>
              </select>
            </label>
            <label>
              Every{' '}
              <select value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
                <option value={900}>15 min</option>
                <option value={3600}>hour</option>
                <option value={86400}>day</option>
              </select>
            </label>
            <button className="primary-small" disabled={busy || !dataset.trim()} onClick={submit}>
              {busy ? 'Creating…' : 'Create sync workflow'}
            </button>
          </div>
          {error && <p className="error-text">{error}</p>}
        </>
      )}
    </section>
  )
}

interface Verification {
  claim: string
  sql: string
  result?: unknown
  verdict: string
  note?: string
}

function AnalyzeSection({ sourceId }: { sourceId: string }) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [insight, setInsight] = useState<string | null>(null)
  const [verifications, setVerifications] = useState<Verification[]>([])
  const [meta, setMeta] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await analyzeSource(sourceId, question || undefined)
      setInsight(result.insight)
      setVerifications(
        ((result as unknown as { verifications?: Verification[] }).verifications) ?? [],
      )
      setMeta(`${result.model} · analyzed ${result.sampled_rows} of ${result.total_rows} rows`)
    } catch (err) {
      setError((err as Error).message)
      setInsight(null)
      setVerifications([])
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
          {verifications.length > 0 && (
            <div className="verification-list">
              {verifications.map((verification, index) => (
                <details key={index} className="output-paths">
                  <summary>
                    {verification.verdict === 'confirmed'
                      ? '✅'
                      : verification.verdict === 'corrected'
                        ? '✏️'
                        : '❓'}{' '}
                    {verification.claim}
                    {verification.note ? ` — ${verification.note}` : ''}
                  </summary>
                  <pre className="cell-sql">{verification.sql}</pre>
                  {verification.result != null && (
                    <pre className="cell-sql">
                      {JSON.stringify(verification.result, null, 1)}
                    </pre>
                  )}
                </details>
              ))}
            </div>
          )}
          {meta && (
            <p className="config-subtitle">
              {meta}
              {verifications.length > 0 &&
                ` · ${verifications.filter((v) => v.verdict === 'confirmed').length}/${verifications.length} claims verified by query`}
            </p>
          )}
        </div>
      )}
    </section>
  )
}
