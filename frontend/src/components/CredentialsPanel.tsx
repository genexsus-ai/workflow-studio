import { useEffect, useRef, useState } from 'react'

import * as api from '../api'
import type { ConnectorDef, CredentialSummary, OAuthProvidersResponse } from '../types'

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
  const [oauth, setOauth] = useState<OAuthProvidersResponse | null>(null)
  const [connecting, setConnecting] = useState(false)
  const pollTimer = useRef<number | null>(null)

  useEffect(() => {
    api.listOAuthProviders().then(setOauth).catch(() => setOauth(null))
    return () => {
      if (pollTimer.current !== null) clearInterval(pollTimer.current)
    }
  }, [])

  const selected = connectors.find((c) => c.type === (type || connectors[0]?.type))
  const oauthProvider = selected?.oauth_provider
    ? oauth?.providers.find((p) => p.provider === selected.oauth_provider)
    : undefined

  const connectViaOAuth = async () => {
    if (!oauthProvider || !name) return
    try {
      setError(null)
      const { authorize_url } = await api.startOAuth(oauthProvider.provider, name)
      window.open(authorize_url, '_blank', 'width=600,height=750')
      setConnecting(true)
      // The popup stores the credential server-side; poll until it appears
      const expectedName = name
      let ticks = 0
      pollTimer.current = window.setInterval(async () => {
        ticks += 1
        const current = await api.listCredentials().catch(() => [])
        if (current.some((c) => c.name === expectedName) || ticks > 120) {
          if (pollTimer.current !== null) clearInterval(pollTimer.current)
          pollTimer.current = null
          setConnecting(false)
          if (current.some((c) => c.name === expectedName)) {
            setAdding(false)
            setName('')
            onChanged()
          }
        }
      }, 1000)
    } catch (err) {
      setError((err as Error).message)
    }
  }

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
          {oauthProvider && (
            <div className="oauth-connect">
              <button
                className="primary-small"
                disabled={!name || !oauthProvider.app_configured || connecting}
                title={
                  oauthProvider.app_configured
                    ? `Authorize via ${oauthProvider.label}`
                    : `Register the ${oauthProvider.label} OAuth app below first`
                }
                onClick={connectViaOAuth}
              >
                {connecting ? 'Waiting for authorization…' : `🔗 Connect ${oauthProvider.label} account`}
              </button>
              <span className="config-subtitle">— or paste a token below</span>
            </div>
          )}
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

      {oauth && oauth.providers.length > 0 && (
        <details className="rail-subsection">
          <summary>OAuth apps</summary>
          <p className="config-subtitle">
            Register your own OAuth app per provider. Redirect URI:{' '}
            <code>{oauth.redirect_uri}</code>
          </p>
          {oauth.providers.map((provider) => (
            <OAuthAppForm
              key={provider.provider}
              provider={provider}
              onSaved={() => api.listOAuthProviders().then(setOauth).catch(() => undefined)}
            />
          ))}
        </details>
      )}
    </details>
  )
}

function OAuthAppForm({
  provider,
  onSaved,
}: {
  provider: { provider: string; label: string; app_configured: boolean }
  onSaved: () => void
}) {
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [busy, setBusy] = useState(false)

  return (
    <div className="oauth-app-form">
      <strong>
        {provider.label} {provider.app_configured ? '✓' : ''}
      </strong>
      <input
        value={clientId}
        placeholder="client ID"
        onChange={(e) => setClientId(e.target.value)}
      />
      <input
        type="password"
        value={clientSecret}
        placeholder="client secret"
        onChange={(e) => setClientSecret(e.target.value)}
      />
      <button
        className="primary-small"
        disabled={busy || !clientId || !clientSecret}
        onClick={async () => {
          setBusy(true)
          try {
            await api.saveOAuthApp(provider.provider, clientId, clientSecret)
            setClientId('')
            setClientSecret('')
            onSaved()
          } finally {
            setBusy(false)
          }
        }}
      >
        Save
      </button>
    </div>
  )
}
