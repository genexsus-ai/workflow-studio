/** Icon per node type, shared by the canvas nodes and the palette. */
export const NODE_ICONS: Record<string, string> = {
  trigger: '⚡',
  input: '📥',
  output: '📤',
  agent: '🤖',
  tool: '🛠️',
  connector: '🔌',
  mcp: '🧩',
  decision: '🔀',
  loop: '🔁',
  flow: '👥',
  model: '🧠',
  memory: '💾',
  subworkflow: '🗂️',
}

export function nodeIcon(type: string, config?: Record<string, unknown>): string {
  if (type === 'trigger' && config?.trigger_kind === 'schedule') return '🕐'
  return NODE_ICONS[type] ?? '⬡'
}
