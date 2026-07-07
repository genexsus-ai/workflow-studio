import { useState } from 'react'

import { apiBase } from '../api'
import type { AutomationConfig } from '../types'

interface AutomationPanelProps {
  workflowId: string | null
  automation: AutomationConfig
  onChange: (config: AutomationConfig) => Promise<void>
  sharedMemory: boolean
  onSharedMemoryChange: (value: boolean) => void
}

export function AutomationPanel({
  workflowId,
  automation,
  onChange,
  sharedMemory,
  onSharedMemoryChange,
}: AutomationPanelProps) {
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const sharedMemoryToggle = (
    <label className="field field-checkbox" title="All agents in a run share a common memory bus">
      <input
        type="checkbox"
        checked={sharedMemory}
        onChange={(event) => onSharedMemoryChange(event.target.checked)}
      />
      <span>Shared agent memory (save to apply)</span>
    </label>
  )

  if (!workflowId) {
    return (
      <details className="rail-section">
        <summary>Automation</summary>
        {sharedMemoryToggle}
        <p className="config-subtitle">Save the workflow first to enable webhooks and schedules.</p>
      </details>
    )
  }

  const apply = async (config: AutomationConfig) => {
    setBusy(true)
    try {
      await onChange(config)
    } finally {
      setBusy(false)
    }
  }

  const hookUrl = automation.webhook_token
    ? `${apiBase}/hooks/${automation.webhook_token}`
    : null

  return (
    <details className="rail-section" open>
      <summary>Automation</summary>

      {sharedMemoryToggle}

      <label className="field field-checkbox">
        <input
          type="checkbox"
          disabled={busy}
          checked={automation.webhook_enabled}
          onChange={(event) => apply({ ...automation, webhook_enabled: event.target.checked })}
        />
        <span>Webhook — run when this URL is called</span>
      </label>
      {automation.webhook_enabled && (
        <>
          <label className="field">
            <span>Provider</span>
            <select
              disabled={busy}
              value={automation.webhook_provider ?? 'generic'}
              onChange={(event) => apply({ ...automation, webhook_provider: event.target.value })}
            >
              <option value="generic">Generic (any POST)</option>
              <option value="github">GitHub (signed events)</option>
            </select>
          </label>
          {automation.webhook_provider === 'github' && (
            <>
              <label className="field">
                <span>Webhook secret (HMAC)</span>
                <input
                  type="password"
                  disabled={busy}
                  value={automation.webhook_secret ?? ''}
                  placeholder="same secret as in GitHub webhook settings"
                  onChange={(event) =>
                    apply({ ...automation, webhook_secret: event.target.value || null })
                  }
                />
              </label>
              <label className="field">
                <span>Event filter</span>
                <input
                  disabled={busy}
                  value={automation.webhook_event_filter ?? ''}
                  placeholder="e.g. issues.opened (empty = all events)"
                  onChange={(event) =>
                    apply({ ...automation, webhook_event_filter: event.target.value || null })
                  }
                />
              </label>
            </>
          )}
        </>
      )}
      {hookUrl && (
        <div className="hook-url">
          <code>{hookUrl}</code>
          <button
            onClick={() => {
              navigator.clipboard.writeText(hookUrl)
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
            }}
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <p className="config-subtitle">
            POST JSON to this URL; the body becomes the workflow input.
          </p>
        </div>
      )}

      <label className="field field-checkbox">
        <input
          type="checkbox"
          disabled={busy}
          checked={automation.schedule_enabled}
          onChange={(event) => apply({ ...automation, schedule_enabled: event.target.checked })}
        />
        <span>Schedule — run automatically</span>
      </label>
      {automation.schedule_enabled && (
        <label className="field">
          <span>Every (seconds)</span>
          <input
            type="number"
            min={5}
            disabled={busy}
            value={automation.interval_seconds}
            onChange={(event) =>
              apply({ ...automation, interval_seconds: Math.max(5, Number(event.target.value)) })
            }
          />
        </label>
      )}
    </details>
  )
}
