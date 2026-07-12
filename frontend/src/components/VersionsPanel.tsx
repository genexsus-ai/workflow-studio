import { useCallback, useEffect, useState } from 'react'

import { listWorkflowVersions, restoreWorkflowVersion } from '../api'
import type { WorkflowVersionInfo } from '../types'

interface VersionsPanelProps {
  workflowId: string | null
  /** Bumped by the parent after each save so the list refreshes. */
  refreshKey: number
  onRestored: (workflowId: string) => void
}

export function VersionsPanel({ workflowId, refreshKey, onRestored }: VersionsPanelProps) {
  const [versions, setVersions] = useState<WorkflowVersionInfo[]>([])
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    if (!workflowId) {
      setVersions([])
      return
    }
    listWorkflowVersions(workflowId)
      .then(setVersions)
      .catch(() => setVersions([]))
  }, [workflowId])

  useEffect(refresh, [refresh, refreshKey])

  const restore = async (version: string) => {
    if (!workflowId) return
    setBusy(true)
    try {
      await restoreWorkflowVersion(workflowId, version)
      onRestored(workflowId)
      refresh()
    } finally {
      setBusy(false)
    }
  }

  if (!workflowId) return null

  return (
    <details className="rail-section">
      <summary>
        Versions {versions.length > 0 && <span className="runs-count">({versions.length})</span>}
      </summary>
      {versions.length === 0 ? (
        <p className="config-subtitle">
          No earlier versions yet — one is kept every time you save.
        </p>
      ) : (
        versions.map((version) => (
          <div key={version.version} className="credential-row">
            <span title={version.name}>
              {new Date(version.saved_at).toLocaleString()}{' '}
              <em>({version.node_count} nodes)</em>
            </span>
            <button
              disabled={busy}
              title="Restore this version (the current state is kept in history)"
              onClick={() => restore(version.version)}
            >
              ⤺
            </button>
          </div>
        ))
      )}
    </details>
  )
}
