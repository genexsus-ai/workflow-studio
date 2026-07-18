import { useEffect, useState } from 'react'

import { apiBase } from '../api'
import { KeyValueOutput } from './NodeIOView'

import type { StudioNode } from '../lib/translate'
import type { AutomationConfig, FormField } from '../types'

interface FormTriggerEditorProps {
  node: StudioNode
  automation?: AutomationConfig
  /** Latest run input — a form submission — shown as the trigger's output. */
  workflowInput: Record<string, unknown> | null
  onConfigChange: (nodeId: string, config: Record<string, unknown>, label?: string) => void
  onSaveWorkflow?: () => void
  onClose: () => void
}

const slugify = (value: string) =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')

/**
 * n8n-style node detail editor for form triggers: form settings and field
 * cards on the left, the latest submission (the trigger's output) on the
 * right.
 */
export function FormTriggerEditor({
  node,
  automation,
  workflowInput,
  onConfigChange,
  onSaveWorkflow,
  onClose,
}: FormTriggerEditorProps) {
  const [copied, setCopied] = useState(false)
  const config = node.data.config
  const fields = (config.form_fields as FormField[] | undefined) ?? []

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const setValue = (name: string, value: unknown) =>
    onConfigChange(node.id, { ...config, [name]: value })

  const setFields = (next: FormField[]) => setValue('form_fields', next)
  const updateField = (index: number, patch: Partial<FormField>) =>
    setFields(fields.map((field, i) => (i === index ? { ...field, ...patch } : field)))

  const formUrl =
    automation?.form_enabled && automation.form_token
      ? `${apiBase}/forms/${automation.form_token}`
      : null

  return (
    <div className="node-io-overlay" onClick={onClose}>
      <div className="node-io-dialog form-editor" onClick={(event) => event.stopPropagation()}>
        <div className="node-io-header">
          <h2>
            <span className="form-editor-icon">📝</span>
            On form submission
            <span className="node-io-type">form trigger</span>
          </h2>
          <div className="form-editor-header-actions">
            {onSaveWorkflow && (
              <button className="primary form-editor-save" onClick={onSaveWorkflow}>
                {formUrl ? '✓ Save changes' : '✓ Save & create form'}
              </button>
            )}
            <button className="form-editor-close" onClick={onClose} title="Close (Esc)">
              ✕
            </button>
          </div>
        </div>

        <div className="node-io-columns form-editor-columns">
          <div className="node-io-col form-editor-params">
            <h3>Form</h3>

            {formUrl ? (
              <div className="form-editor-url">
                <span className="form-editor-url-label">Form URL</span>
                <div className="form-editor-url-row">
                  <code>{formUrl}</code>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(formUrl)
                      setCopied(true)
                      setTimeout(() => setCopied(false), 1500)
                    }}
                  >
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                  <a href={formUrl} target="_blank" rel="noreferrer">
                    Open ↗
                  </a>
                </div>
              </div>
            ) : (
              <p className="node-io-empty">
                Save the workflow to create the form and get its shareable URL.
              </p>
            )}

            <label className="field">
              <span>Form title</span>
              <input
                value={String(config.form_title ?? '')}
                placeholder="e.g. Contact us"
                onChange={(event) => setValue('form_title', event.target.value)}
              />
            </label>
            <label className="field">
              <span>Form description</span>
              <textarea
                rows={2}
                value={String(config.form_description ?? '')}
                placeholder="e.g. We'll get back to you soon"
                onChange={(event) => setValue('form_description', event.target.value)}
              />
            </label>

            <h3 className="form-editor-elements-title">Form elements</h3>
            {fields.length === 0 && (
              <p className="node-io-empty">No fields yet — add your first form element.</p>
            )}
            {fields.map((field, index) => (
              <div className="form-editor-card" key={index}>
                <div className="form-editor-card-grid">
                  <label>
                    <span>Field label</span>
                    <input
                      value={field.label ?? ''}
                      placeholder="First Name"
                      onChange={(event) => updateField(index, { label: event.target.value })}
                      onBlur={(event) => {
                        // Auto-derive the input key from the label until the
                        // user sets one explicitly.
                        if (!field.name || field.name.startsWith('field_')) {
                          const name = slugify(event.target.value)
                          if (name) updateField(index, { label: event.target.value, name })
                        }
                      }}
                    />
                  </label>
                  <label>
                    <span>
                      Field name <em>({'{{ input.<name> }}'})</em>
                    </span>
                    <input
                      value={field.name}
                      placeholder="first_name"
                      spellCheck={false}
                      onChange={(event) => updateField(index, { name: event.target.value })}
                    />
                  </label>
                  <label>
                    <span>Element type</span>
                    <select
                      value={field.type ?? 'text'}
                      onChange={(event) => updateField(index, { type: event.target.value })}
                    >
                      <option value="text">Text</option>
                      <option value="textarea">Long text</option>
                      <option value="number">Number</option>
                      <option value="select">Dropdown</option>
                    </select>
                  </label>
                  <label>
                    <span>Placeholder</span>
                    <input
                      value={field.placeholder ?? ''}
                      placeholder="Shown inside the empty field"
                      onChange={(event) => updateField(index, { placeholder: event.target.value })}
                    />
                  </label>
                </div>
                {(field.type ?? 'text') === 'select' && (
                  <label className="form-editor-options">
                    <span>Options (comma-separated)</span>
                    <input
                      value={(field.options ?? []).join(', ')}
                      placeholder="Low, Medium, High"
                      onChange={(event) =>
                        updateField(index, {
                          options: event.target.value
                            .split(',')
                            .map((option) => option.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </label>
                )}
                <div className="form-editor-card-footer">
                  <label className="form-editor-required">
                    <input
                      type="checkbox"
                      checked={field.required ?? false}
                      onChange={(event) => updateField(index, { required: event.target.checked })}
                    />
                    Required field
                  </label>
                  <button
                    className="form-editor-remove"
                    title="Remove element"
                    onClick={() => setFields(fields.filter((_, i) => i !== index))}
                  >
                    🗑 Remove
                  </button>
                </div>
              </div>
            ))}
            <button
              className="form-editor-add"
              onClick={() => setFields([...fields, { name: '', label: '', type: 'text' }])}
            >
              ＋ Add form element
            </button>
          </div>

          <div className="node-io-col">
            <h3>Output</h3>
            {workflowInput ? (
              <>
                <p className="form-editor-output-hint">
                  Latest submission — downstream nodes receive these values as{' '}
                  <code>{'{{ input.<name> }}'}</code>.
                </p>
                <div className="node-io-block">
                  <KeyValueOutput data={workflowInput} />
                </div>
              </>
            ) : (
              <p className="node-io-empty">
                <span className="form-editor-listening" /> Waiting for submissions… open the form
                URL, submit it, and the data appears here within a few seconds. (Make sure you're
                submitting <em>this</em> workflow's URL — each workflow has its own.)
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
