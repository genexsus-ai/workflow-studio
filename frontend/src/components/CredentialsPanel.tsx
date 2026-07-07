import { useState } from 'react'

import * as api from '../api'
import type { ConnectorDef, CredentialSummary } from '../types'

interface CredentialsPanelProps {
  connectors: ConnectorDef[]
  credentials: CredentialSummary[]
  onChanged: () => void
}

export function CredentialsPanel({ connectors, credentials, onChanged }: CredentialsPanelProps) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [type, setType] = useState(connectors[0]?.type ?? '')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const selected = connectors.find((c) => c.type === (type || connectors[0]?.type))

  const submit = async () => {
    try {
      setError(null)
      await api.createCredential(name, selected?.type ?? type, fields)
      setAdding(false)
      setName('')
      setFields({})
      onChanged()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <details className="rail-section">
      <summary>Credentials {credentials.length > 0 && <span className="runs-count">({credentials.length})</span>}</summary>

      {credentials.map((cred) => (
        <div key={cred.name} className="credential-row">
          <span>
            <strong>{cred.name}</strong> <em>({cred.connector_type})</em>
          </span>
          <button
            className="danger"
            onClick={async () => {
              await api.deleteCredential(cred.name)
              onChanged()
            }}
          >
            ✕
          </button>
        </div>
      ))}
      {credentials.length === 0 && !adding && (
        <p className="config-subtitle">No credentials yet. Add one to use Connector nodes.</p>
      )}

      {adding ? (
        <div className="credential-form">
          <label className="field">
            <span>Name</span>
            <input value={name} placeholder="team-slack" onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Integration</span>
            <select
              value={selected?.type ?? ''}
              onChange={(e) => {
                setType(e.target.value)
                setFields({})
              }}
            >
              {connectors.map((c) => (
                <option key={c.type} value={c.type}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          {selected?.credential_fields.map((field) => (
            <label className="field" key={field.name}>
              <span>{field.name}</span>
              <input
                type={field.secret ? 'password' : 'text'}
                value={fields[field.name] ?? ''}
                placeholder={field.example}
                onChange={(e) => setFields({ ...fields, [field.name]: e.target.value })}
              />
            </label>
          ))}
          {error && <p className="error-text">{error}</p>}
          <div className="credential-form-actions">
            <button className="primary-small" onClick={submit} disabled={!name}>
              Save credential
            </button>
            <button onClick={() => setAdding(false)}>Cancel</button>
          </div>
          <p className="config-subtitle">
            Secrets are stored on the backend and never sent back to the browser.
          </p>
        </div>
      ) : (
        <button className="refresh-button" onClick={() => setAdding(true)}>
          + Add credential
        </button>
      )}
    </details>
  )
}
