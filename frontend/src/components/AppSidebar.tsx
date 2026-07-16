import { useEffect, useState } from 'react'

import { FeedbackDialog } from './FeedbackDialog'

export type AppId = 'workflow' | 'analytics' | 'datascience'

interface AppEntry {
  id: AppId
  icon: string
  label: string
  available: boolean
}

const APPS: AppEntry[] = [
  { id: 'workflow', icon: '🧩', label: 'Workflow Studio', available: true },
  { id: 'analytics', icon: '📊', label: 'Analytics', available: true },
  { id: 'datascience', icon: '🧪', label: 'Data Science', available: true },
]

const COLLAPSE_KEY = 'genxai-sidebar-collapsed'

interface AppSidebarProps {
  active: AppId
  onSelect: (app: AppId) => void
}

export function AppSidebar({ active, onSelect }: AppSidebarProps) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === '1',
  )
  const [feedbackOpen, setFeedbackOpen] = useState(false)

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  return (
    <nav className={`app-sidebar${collapsed ? ' collapsed' : ''}`}>
      <button
        type="button"
        className="app-sidebar-brand"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        onClick={() => setCollapsed((value) => !value)}
      >
        <span className="app-sidebar-logo">⚛</span>
        {!collapsed && <span className="app-sidebar-title">GenXAI</span>}
        {!collapsed && <span className="app-sidebar-chevron">«</span>}
      </button>

      <div className="app-sidebar-items">
        {APPS.map((app) => (
          <button
            key={app.id}
            type="button"
            className={`app-sidebar-item${active === app.id ? ' active' : ''}`}
            title={collapsed ? app.label : app.available ? undefined : 'Coming soon'}
            onClick={() => onSelect(app.id)}
          >
            <span className="app-sidebar-icon">{app.icon}</span>
            {!collapsed && (
              <span className="app-sidebar-label">
                {app.label}
                {!app.available && <em className="app-sidebar-soon">soon</em>}
              </span>
            )}
          </button>
        ))}
      </div>

      <button
        type="button"
        className="app-sidebar-item app-sidebar-feedback"
        title="Send feedback"
        onClick={() => setFeedbackOpen(true)}
      >
        <span className="app-sidebar-icon">💬</span>
        {!collapsed && <span className="app-sidebar-label">Feedback</span>}
      </button>

      <button
        type="button"
        className="app-sidebar-collapse"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        onClick={() => setCollapsed((value) => !value)}
      >
        {collapsed ? '»' : '«'}
      </button>
      {feedbackOpen && <FeedbackDialog onClose={() => setFeedbackOpen(false)} />}
    </nav>
  )
}
