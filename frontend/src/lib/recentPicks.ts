/** Recently-picked node picker entries, persisted in localStorage. */

const KEY = 'genxai-studio-recent-picks'
const MAX = 8

export function loadRecents(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((k) => typeof k === 'string') : []
  } catch {
    return []
  }
}

export function saveRecent(key: string): void {
  try {
    const next = [key, ...loadRecents().filter((k) => k !== key)].slice(0, MAX)
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* storage unavailable — recents are best-effort */
  }
}
