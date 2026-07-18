import { useState } from 'react'

import { apiBase } from '../api'
import type { AutomationConfig, FormField, WorkflowSummary } from '../types'

interface AutomationPanelProps {
  workflowId: string | null
  automation: AutomationConfig
  workflows: WorkflowSummary[]
  onChange: (config: AutomationConfig) => Promise<void>
  sharedMemory: boolean
  onSharedMemoryChange: (value: boolean) => void
  hasTrigger: boolean
}

export function AutomationPanel({
  workflowId,
  automation,
  workflows,
  onChange,
  sharedMemory,
  onSharedMemoryChange,
  hasTrigger,
}: AutomationPanelProps) {
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  // Drafts so partial cron/timezone text isn't validated on every keystroke
  const [cronDraft, setCronDraft] = useState<string | null>(null)
  const [tzDraft, setTzDraft] = useState<string | null>(null)

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
        <p className="config-subtitle">Save the workflow first to enable webhooks, forms and schedules.</p>
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

      {/* Schedule/webhook live on the trigger node when one exists — it is
          authoritative, so avoid a second, conflicting source of truth. */}
      {hasTrigger && (
        <p className="config-subtitle">
          Schedule, webhook &amp; form are set on the trigger node on the canvas
          {automation.form_enabled ? ' — its form fields are edited below.' : '.'}
        </p>
      )}

      {!hasTrigger && (
      <label className="field field-checkbox">
        <input
          type="checkbox"
          disabled={busy}
          checked={automation.webhook_enabled}
          onChange={(event) => apply({ ...automation, webhook_enabled: event.target.checked })}
        />
        <span>Webhook — run when this URL is called</span>
      </label>
      )}
      {!hasTrigger && automation.webhook_enabled && (
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

      {!hasTrigger && (
      <label className="field field-checkbox">
        <input
          type="checkbox"
          disabled={busy}
          checked={automation.form_enabled ?? false}
          onChange={(event) => apply({ ...automation, form_enabled: event.target.checked })}
        />
        <span>Form — run on form submission</span>
      </label>
      )}
      {automation.form_enabled && (
        <>
          <label className="field">
            <span>Form title</span>
            <input
              disabled={busy}
              value={automation.form_title ?? ''}
              placeholder="e.g. Contact us"
              onChange={(event) => apply({ ...automation, form_title: event.target.value || null })}
            />
          </label>
          <label className="field">
            <span>Form description (optional)</span>
            <input
              disabled={busy}
              value={automation.form_description ?? ''}
              placeholder="Shown under the title"
              onChange={(event) =>
                apply({ ...automation, form_description: event.target.value || null })
              }
            />
          </label>
          <FormFieldsEditor
            fields={automation.form_fields ?? []}
            busy={busy}
            onChange={(fields) => apply({ ...automation, form_fields: fields })}
          />
          {automation.form_token && (
            <div className="hook-url">
              <code>{`${apiBase}/forms/${automation.form_token}`}</code>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(`${apiBase}/forms/${automation.form_token}`)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1500)
                }}
              >
                {copied ? 'Copied!' : 'Copy'}
              </button>
              <p className="config-subtitle">
                Share this URL — each submission runs the workflow with the field values as{' '}
                <code>{'{{ input.<name> }}'}</code>.
              </p>
            </div>
          )}
        </>
      )}

      {!hasTrigger && (
      <label className="field field-checkbox">
        <input
          type="checkbox"
          disabled={busy}
          checked={automation.schedule_enabled}
          onChange={(event) => apply({ ...automation, schedule_enabled: event.target.checked })}
        />
        <span>Schedule — run automatically</span>
      </label>
      )}
      {!hasTrigger && automation.schedule_enabled && (
        <>
          <label className="field">
            <span>Cron expression (optional)</span>
            <input
              disabled={busy}
              value={cronDraft ?? automation.schedule_cron ?? ''}
              placeholder="e.g. 0 9 * * 1-5 — overrides interval"
              onChange={(event) => setCronDraft(event.target.value)}
              onBlur={() => {
                if (cronDraft === null) return
                const cron = cronDraft.trim() || null
                setCronDraft(null)
                if (cron !== (automation.schedule_cron ?? null)) {
                  apply({ ...automation, schedule_cron: cron })
                }
              }}
            />
          </label>
          {automation.schedule_cron && (
            <label className="field">
              <span>Timezone (IANA)</span>
              <input
                disabled={busy}
                value={tzDraft ?? automation.schedule_timezone ?? 'UTC'}
                placeholder="e.g. America/New_York"
                onChange={(event) => setTzDraft(event.target.value)}
                onBlur={() => {
                  if (tzDraft === null) return
                  const timezone = tzDraft.trim() || 'UTC'
                  setTzDraft(null)
                  if (timezone !== (automation.schedule_timezone ?? 'UTC')) {
                    apply({ ...automation, schedule_timezone: timezone })
                  }
                }}
              />
            </label>
          )}
          {!automation.schedule_cron && (
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
        </>
      )}

      <label className="field">
        <span>On failure, run</span>
        <select
          disabled={busy}
          value={automation.error_workflow_id ?? ''}
          onChange={(event) =>
            apply({ ...automation, error_workflow_id: event.target.value || null })
          }
        >
          <option value="">— nothing —</option>
          {workflows
            .filter((workflow) => workflow.id !== workflowId)
            .map((workflow) => (
              <option key={workflow.id} value={workflow.id}>
                {workflow.name}
              </option>
            ))}
        </select>
      </label>
      {automation.error_workflow_id && (
        <p className="config-subtitle">
          The handler receives {'{ error, failed_run_id, workflow_name, failed_nodes, input }'} as
          its run input.
        </p>
      )}
    </details>
  )
}

function FormFieldsEditor({
  fields,
  busy,
  onChange,
}: {
  fields: FormField[]
  busy: boolean
  onChange: (fields: FormField[]) => void
}) {
  const update = (index: number, patch: Partial<FormField>) =>
    onChange(fields.map((field, i) => (i === index ? { ...field, ...patch } : field)))

  return (
    <div className="form-fields-editor">
      <span className="form-fields-title">Form fields</span>
      {fields.length === 0 && (
        <p className="config-subtitle">No fields yet — add at least one.</p>
      )}
      {fields.map((field, index) => (
        <div className="form-field-row" key={index}>
          <div className="form-field-main">
            <input
              disabled={busy}
              defaultValue={field.name}
              placeholder="name (input key)"
              spellCheck={false}
              onBlur={(event) => {
                const name = event.target.value.trim()
                if (name && name !== field.name) update(index, { name })
              }}
            />
            <input
              disabled={busy}
              defaultValue={field.label ?? ''}
              placeholder="Label shown on the form"
              onBlur={(event) => {
                if (event.target.value !== (field.label ?? '')) {
                  update(index, { label: event.target.value })
                }
              }}
            />
            <select
              disabled={busy}
              value={field.type ?? 'text'}
              onChange={(event) => update(index, { type: event.target.value })}
            >
              <option value="text">Text</option>
              <option value="textarea">Long text</option>
              <option value="number">Number</option>
              <option value="select">Dropdown</option>
            </select>
            <label className="form-field-required" title="Required">
              <input
                type="checkbox"
                disabled={busy}
                checked={field.required ?? false}
                onChange={(event) => update(index, { required: event.target.checked })}
              />
              req
            </label>
            <button
              disabled={busy}
              title="Remove field"
              onClick={() => onChange(fields.filter((_, i) => i !== index))}
            >
              ✕
            </button>
          </div>
          {(field.type ?? 'text') === 'select' && (
            <input
              disabled={busy}
              defaultValue={(field.options ?? []).join(', ')}
              placeholder="Options, comma-separated"
              onBlur={(event) => {
                const options = event.target.value
                  .split(',')
                  .map((option) => option.trim())
                  .filter(Boolean)
                update(index, { options })
              }}
            />
          )}
        </div>
      ))}
      <button
        disabled={busy}
        onClick={() =>
          onChange([...fields, { name: `field_${fields.length + 1}`, type: 'text' }])
        }
      >
        + Add field
      </button>
    </div>
  )
}
