/** Which collaboration patterns can be drawn as plain wired agent nodes.
 *
 * Graph-shaped patterns (chains, fan-out, fan-in) expand into real agent
 * nodes connected by flow edges — the collaboration is visible on the
 * canvas and runs on the ordinary graph engine. Logic-shaped patterns
 * (critic loops, voting, auctions, packet routing, peer rounds) cannot be
 * expressed with plain edges and stay as a single team node whose members
 * hang off its Agents port.
 */
export type ExpansionShape = 'chain' | 'fan_out' | 'fan_in' | 'parallel'

export const EXPANDABLE_PATTERNS: Record<string, ExpansionShape> = {
  round_robin: 'chain', // A → B → C in listed order
  coordinator_worker: 'fan_out', // first agent → the rest in parallel
  map_reduce: 'fan_in', // all but last in parallel → last merges
  parallel: 'parallel', // side-by-side, no internal edges
}

export const expansionHint = (patternId: string): string =>
  patternId in EXPANDABLE_PATTERNS
    ? 'Expands into wired agents on the canvas.'
    : 'Runs as one team node with attached agents.'
