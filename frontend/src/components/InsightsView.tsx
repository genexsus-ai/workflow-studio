import { useCallback, useEffect, useState } from 'react'

import { getInsights } from '../api'
import type { InsightsData, InsightsDaily } from '../types'

// Status colors (validated against the app surface #f8fafc: contrast >= 3:1,
// CVD dE 12.4) — state, not series identity, so they come from the status
// palette and always ship with labels.
const STATUS_GOOD = '#0ca30c'
const STATUS_CRITICAL = '#d03b3b'
const SEQUENTIAL_BLUE = '#2a78d6'

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}

export function InsightsView() {
  const [days, setDays] = useState(14)
  const [data, setData] = useState<InsightsData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    getInsights(days)
      .then((insights) => {
        setData(insights)
        setError(null)
      })
      .catch((err) => setError((err as Error).message))
  }, [days])

  useEffect(refresh, [refresh])

  if (error) {
    return (
      <div className="insights">
        <p className="error-text">Could not load insights: {error}</p>
      </div>
    )
  }
  if (!data) return <div className="insights" />

  const { totals } = data

  return (
    <div className="insights">
      <div className="insights-header">
        <h1>Insights</h1>
        <div className="insights-filters">
          <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          <button onClick={refresh}>↻ Refresh</button>
        </div>
      </div>

      <div className="stat-row">
        <StatTile label="Runs" value={String(totals.runs)} />
        <StatTile
          label="Success rate"
          value={totals.success_rate == null ? '—' : `${Math.round(totals.success_rate * 100)}%`}
        />
        <StatTile label="Failed runs" value={String(totals.failed)} />
        <StatTile label="Median duration" value={formatDuration(totals.median_duration_ms)} />
      </div>

      <section className="insights-card">
        <h2>Runs per day</h2>
        <DailyRunsChart daily={data.daily} />
        <div className="chart-legend">
          <span>
            <i className="legend-chip" style={{ background: STATUS_GOOD }} /> Succeeded
          </span>
          <span>
            <i className="legend-chip" style={{ background: STATUS_CRITICAL }} /> Failed
          </span>
        </div>
      </section>

      <div className="insights-columns">
        <section className="insights-card">
          <h2>By trigger</h2>
          {data.triggers.length === 0 ? (
            <p className="config-subtitle">No runs in this period.</p>
          ) : (
            <TriggerBars triggers={data.triggers} />
          )}
        </section>

        <section className="insights-card">
          <h2>Slowest nodes</h2>
          {data.slowest_nodes.length === 0 ? (
            <p className="config-subtitle">No node timings recorded yet.</p>
          ) : (
            <table className="insights-table">
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Workflow</th>
                  <th className="num">Avg</th>
                  <th className="num">Runs</th>
                </tr>
              </thead>
              <tbody>
                {data.slowest_nodes.map((node) => (
                  <tr key={`${node.workflow}:${node.node_id}`}>
                    <td>
                      <code>{node.node_id}</code>
                    </td>
                    <td className="truncate">{node.workflow}</td>
                    <td className="num">{formatDuration(node.avg_duration_ms)}</td>
                    <td className="num">{node.runs}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <section className="insights-card">
        <h2>By workflow</h2>
        {data.workflows.length === 0 ? (
          <p className="config-subtitle">No runs in this period.</p>
        ) : (
          <table className="insights-table">
            <thead>
              <tr>
                <th>Workflow</th>
                <th className="num">Runs</th>
                <th className="num">Succeeded</th>
                <th className="num">Failed</th>
                <th className="num">Success rate</th>
                <th className="num">Avg duration</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {data.workflows.map((workflow) => (
                <tr key={workflow.name}>
                  <td className="truncate">{workflow.name}</td>
                  <td className="num">{workflow.runs}</td>
                  <td className="num">{workflow.succeeded}</td>
                  <td className="num">{workflow.failed}</td>
                  <td className="num">{Math.round(workflow.success_rate * 100)}%</td>
                  <td className="num">{formatDuration(workflow.avg_duration_ms)}</td>
                  <td>
                    {workflow.last_run_at
                      ? new Date(workflow.last_run_at).toLocaleString()
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-tile-value">{value}</span>
      <span className="stat-tile-label">{label}</span>
    </div>
  )
}

function DailyRunsChart({ daily }: { daily: InsightsDaily[] }) {
  const [hover, setHover] = useState<InsightsDaily | null>(null)

  const width = 640
  const height = 160
  const padBottom = 18
  const gap = 2 // surface gap between stacked segments and between columns
  const max = Math.max(1, ...daily.map((d) => d.succeeded + d.failed))
  const slot = width / daily.length
  const barWidth = Math.max(6, Math.min(28, slot - 8))
  const scale = (count: number) => (count / max) * (height - padBottom - 8)

  return (
    <div className="daily-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Workflow runs per day, succeeded and failed"
      >
        {/* recessive baseline */}
        <line
          x1={0}
          y1={height - padBottom}
          x2={width}
          y2={height - padBottom}
          stroke="#e2e8f0"
        />
        {daily.map((day, index) => {
          const x = index * slot + (slot - barWidth) / 2
          const successHeight = scale(day.succeeded)
          const failHeight = scale(day.failed)
          const successY = height - padBottom - successHeight
          const failY = successY - (failHeight > 0 ? gap : 0) - failHeight
          const showTick = daily.length <= 14 || index % 5 === 0
          return (
            <g
              key={day.date}
              onMouseEnter={() => setHover(day)}
              onMouseLeave={() => setHover(null)}
            >
              {/* hit target wider than the marks */}
              <rect x={index * slot} y={0} width={slot} height={height} fill="transparent" />
              {day.succeeded > 0 && (
                <rect
                  x={x}
                  y={successY}
                  width={barWidth}
                  height={Math.max(successHeight, 2)}
                  rx={failHeight > 0 ? 0 : 4}
                  fill={STATUS_GOOD}
                />
              )}
              {day.failed > 0 && (
                <rect
                  x={x}
                  y={failY}
                  width={barWidth}
                  height={Math.max(failHeight, 2)}
                  rx={4}
                  fill={STATUS_CRITICAL}
                />
              )}
              {showTick && (
                <text
                  x={index * slot + slot / 2}
                  y={height - 4}
                  textAnchor="middle"
                  className="chart-tick"
                >
                  {day.date.slice(5)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {hover && (
        <div className="chart-tooltip">
          <strong>{hover.date}</strong> — {hover.succeeded} succeeded, {hover.failed} failed
          {hover.other > 0 ? `, ${hover.other} other` : ''}
        </div>
      )}
    </div>
  )
}

function TriggerBars({ triggers }: { triggers: { trigger: string; runs: number }[] }) {
  const max = Math.max(1, ...triggers.map((t) => t.runs))
  return (
    <div className="trigger-bars">
      {triggers.map((entry) => (
        <div key={entry.trigger} className="trigger-bar-row" title={`${entry.runs} runs`}>
          <span className="trigger-bar-label">{entry.trigger}</span>
          <div className="trigger-bar-track">
            <div
              className="trigger-bar-fill"
              style={{
                width: `${Math.max((entry.runs / max) * 100, 2)}%`,
                background: SEQUENTIAL_BLUE,
              }}
            />
          </div>
          <span className="trigger-bar-value">{entry.runs}</span>
        </div>
      ))}
    </div>
  )
}
