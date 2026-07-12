import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  aggregateDataset,
  analyzeDataset,
  deleteDataset,
  getDatasetRows,
  listDatasets,
} from '../api'
import type { DatasetAggregateEntry, DatasetRowsPage, DatasetSummary } from '../types'

// Single hue for magnitude (validated on the app surface); the chart shows
// one measure, so this is sequential color, not identity.
const SEQUENTIAL_BLUE = '#2a78d6'
const PAGE_SIZE = 25

export function DatasetsView() {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)

  const refresh = useCallback(() => {
    listDatasets()
      .then((list) => {
        setDatasets(list)
        setSelected((current) =>
          current && list.some((d) => d.name === current)
            ? current
            : (list[0]?.name ?? null),
        )
      })
      .catch(() => setDatasets([]))
  }, [])

  useEffect(refresh, [refresh])

  return (
    <div className="datasets">
      <aside className="datasets-list">
        <div className="datasets-list-header">
          <h1>Datasets</h1>
          <button onClick={refresh} title="Refresh">
            ↻
          </button>
        </div>
        {datasets.length === 0 && (
          <p className="config-subtitle">
            No datasets yet. Add a <code>dataset_write</code> node to a workflow —
            each run appends its rows here.
          </p>
        )}
        {datasets.map((dataset) => (
          <button
            key={dataset.name}
            className={`dataset-item${selected === dataset.name ? ' active' : ''}`}
            onClick={() => setSelected(dataset.name)}
          >
            <strong>{dataset.name}</strong>
            <span>
              {dataset.rows} rows
              {dataset.last_written_at &&
                ` · ${new Date(dataset.last_written_at).toLocaleDateString()}`}
            </span>
          </button>
        ))}
      </aside>
      {selected ? (
        <DatasetDetail key={selected} name={selected} onDeleted={refresh} />
      ) : (
        <div className="dataset-detail" />
      )}
    </div>
  )
}

function DatasetDetail({ name, onDeleted }: { name: string; onDeleted: () => void }) {
  const [page, setPage] = useState<DatasetRowsPage | null>(null)
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    getDatasetRows(name, PAGE_SIZE, offset)
      .then(setPage)
      .catch(() => setPage(null))
  }, [name, offset])

  const columns = useMemo(() => {
    const keys = new Set<string>()
    for (const row of page?.rows ?? []) {
      for (const key of Object.keys(row)) {
        if (!key.startsWith('_')) keys.add(key)
      }
    }
    return [...keys].slice(0, 8)
  }, [page])

  const numericColumns = useMemo(
    () =>
      columns.filter((column) =>
        (page?.rows ?? []).some((row) => typeof row[column] === 'number'),
      ),
    [columns, page],
  )

  return (
    <div className="dataset-detail">
      <div className="dataset-detail-header">
        <h2>{name}</h2>
        <span className="config-subtitle">{page?.total ?? 0} rows</span>
        <button
          className="danger"
          title="Delete this dataset"
          onClick={async () => {
            await deleteDataset(name)
            onDeleted()
          }}
        >
          🗑
        </button>
      </div>

      <ChartBuilder name={name} columns={columns} numericColumns={numericColumns} />
      <AnalyzeSection name={name} />

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
                    <th>written</th>
                  </tr>
                </thead>
                <tbody>
                  {page.rows.map((row) => (
                    <tr key={String(row._id)}>
                      {columns.map((column) => (
                        <td key={column} className="truncate">
                          {row[column] == null
                            ? '—'
                            : typeof row[column] === 'object'
                              ? JSON.stringify(row[column])
                              : String(row[column])}
                        </td>
                      ))}
                      <td>{new Date(String(row._created_at)).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="dataset-pager">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                ← Newer
              </button>
              <span className="config-subtitle">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}
              </span>
              <button
                disabled={offset + PAGE_SIZE >= page.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Older →
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function ChartBuilder({
  name,
  columns,
  numericColumns,
}: {
  name: string
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
    aggregateDataset(name, metric, effectiveField, groupBy || undefined)
      .then((result) => {
        setEntries(result.slice(0, 12))
        setError(null)
      })
      .catch((err) => setError((err as Error).message))
  }, [name, metric, field, groupBy, numericColumns])

  const max = Math.max(1, ...entries.map((entry) => entry.value))

  return (
    <section className="insights-card">
      <h2>Chart</h2>
      <div className="insights-filters chart-builder-filters">
        <label>
          Group by{' '}
          <select value={groupBy} onChange={(event) => setGroupBy(event.target.value)}>
            <option value="">— whole dataset —</option>
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
            <select value={field || numericColumns[0] || ''} onChange={(event) => setField(event.target.value)}>
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
            <div
              key={entry.group}
              className="trigger-bar-row"
              title={`${entry.rows} rows`}
            >
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

function AnalyzeSection({ name }: { name: string }) {
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [insight, setInsight] = useState<string | null>(null)
  const [meta, setMeta] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await analyzeDataset(name, question || undefined)
      setInsight(result.insight)
      setMeta(
        `${result.model} · analyzed the newest ${result.sampled_rows} of ${result.total_rows} rows`,
      )
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
