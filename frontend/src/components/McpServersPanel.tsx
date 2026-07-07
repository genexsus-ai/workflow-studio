import { useState } from 'react'

import * as api from '../api'
import type { McpServerSummary } from '../types'

interface McpServersPanelProps {
  servers: McpServerSummary[]
  onChanged: () => void
}

export function McpServersPanel({ servers, onChanged }: McpServersPanelProps) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'mcp_stdio' | 'mcp_http'>('mcp_stdio')
  const [command, setCommand] = useState('')
  const [argsText, setArgsText] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    try {
      setError(null)
      const config =
        transport === 'mcp_stdio'
          ? {
              command,
              args: argsText
                .split(/\s+/)
                .map((arg) => arg.trim())
                .filter(Boolean),
            }
          : { url }
      await api.createMcpServer(name, transport, config)
      setAdding(false)
      setName('')
      setCommand('')
      setArgsText('')
      setUrl('')
      onChanged()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <details className="rail-section">
      <summary>
        MCP servers {servers.length > 0 && <span className="runs-count">({servers.length})</span>}
      </summary>

      {servers.map((server) => (
        <div key={server.name} className="credential-row">
          <span title={server.target}>
            <strong>{server.name}</strong>{' '}
            <em>({server.transport === 'mcp_stdio' ? 'stdio' : 'http'})</em>
          </span>
          <button
            className="danger"
            onClick={async () => {
              await api.deleteMcpServer(server.name)
              onChanged()
            }}
          >
            ✕
          </button>
        </div>
      ))}
      {servers.length === 0 && !adding && (
        <p className="config-subtitle">
          No MCP servers yet. Register one to use MCP Tool nodes — any Model
          Context Protocol server works.
        </p>
      )}

      {adding ? (
        <div className="credential-form">
          <label className="field">
            <span>Name</span>
            <input value={name} placeholder="local-tools" onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Transport</span>
            <select
              value={transport}
              onChange={(e) => setTransport(e.target.value as 'mcp_stdio' | 'mcp_http')}
            >
              <option value="mcp_stdio">stdio (local command)</option>
              <option value="mcp_http">HTTP / SSE (remote URL)</option>
            </select>
          </label>
          {transport === 'mcp_stdio' ? (
            <>
              <label className="field">
                <span>Command</span>
                <input
                  value={command}
                  placeholder="npx / python / uvx ..."
                  onChange={(e) => setCommand(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Arguments (space separated)</span>
                <input
                  value={argsText}
                  placeholder="-y @modelcontextprotocol/server-filesystem /tmp"
                  onChange={(e) => setArgsText(e.target.value)}
                />
              </label>
              <p className="config-subtitle">
                The command runs on the backend host — only register commands you trust.
              </p>
            </>
          ) : (
            <label className="field">
              <span>URL</span>
              <input
                value={url}
                placeholder="https://example.com/mcp (or .../sse)"
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
          )}
          {error && <p className="error-text">{error}</p>}
          <div className="credential-form-actions">
            <button className="primary-small" onClick={submit} disabled={!name}>
              Save server
            </button>
            <button onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <button className="refresh-button" onClick={() => setAdding(true)}>
          + Add MCP server
        </button>
      )}
    </details>
  )
}
