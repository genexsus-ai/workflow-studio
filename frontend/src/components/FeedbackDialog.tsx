import { useState } from 'react'

import { submitFeedback } from '../api'

const CATEGORIES = ['general', 'bug', 'idea', 'question'] as const

interface FeedbackDialogProps {
  onClose: () => void
}

export function FeedbackDialog({ onClose }: FeedbackDialogProps) {
  const [message, setMessage] = useState('')
  const [email, setEmail] = useState('')
  const [category, setCategory] = useState<string>('general')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const send = async () => {
    if (!message.trim()) return
    setBusy(true)
    setError(null)
    try {
      await submitFeedback({
        message: message.trim(),
        email: email.trim() || undefined,
        category,
        page: window.location.pathname,
      })
      setSent(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="feedback-overlay" onClick={onClose}>
      <div className="feedback-dialog" onClick={(event) => event.stopPropagation()}>
        {sent ? (
          <>
            <h2>Thank you! 🙏</h2>
            <p className="config-subtitle">Your feedback has been received.</p>
            <div className="feedback-actions">
              <button className="primary" onClick={onClose}>
                Close
              </button>
            </div>
          </>
        ) : (
          <>
            <h2>Send feedback</h2>
            <p className="config-subtitle">
              Tell us what you think, report a bug, or suggest an idea.
            </p>
            <label className="field">
              <span>Type</span>
              <select value={category} onChange={(event) => setCategory(event.target.value)}>
                {CATEGORIES.map((cat) => (
                  <option key={cat} value={cat}>
                    {cat[0].toUpperCase() + cat.slice(1)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Your feedback</span>
              <textarea
                autoFocus
                rows={5}
                value={message}
                placeholder="What's on your mind?"
                onChange={(event) => setMessage(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Email (optional, so we can reply)</span>
              <input
                type="email"
                value={email}
                placeholder="you@example.com"
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            {error && <p className="error-text">{error}</p>}
            <div className="feedback-actions">
              <button onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button className="primary" onClick={send} disabled={busy || !message.trim()}>
                {busy ? 'Sending…' : 'Send'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
