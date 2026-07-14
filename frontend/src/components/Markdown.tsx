import type { ReactNode } from 'react'

// Minimal, dependency-free markdown renderer for agent-written reports.
// Everything is emitted as React text nodes — no raw HTML ever renders,
// so model output cannot inject markup.

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = []
  const tokens = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let key = 0
  let match: RegExpExecArray | null
  while ((match = tokens.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    const token = match[0]
    if (token.startsWith('**')) {
      parts.push(<strong key={key++}>{token.slice(2, -2)}</strong>)
    } else {
      parts.push(<code key={key++}>{token.slice(1, -1)}</code>)
    }
    last = match.index + token.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

const TABLE_ROW = /^\s*\|.*\|\s*$/
const LIST_ITEM = /^\s*(?:[-*]|\d+[.)])\s+/
const SEPARATOR_CELL = /^:?-{2,}:?$/

export default function Markdown({ text }: { text: string }) {
  const lines = text.split(/\r?\n/)
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i += 1
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.*)/)
    if (heading) {
      const content = inline(heading[2])
      blocks.push(
        heading[1].length <= 1 ? (
          <h3 key={key++}>{content}</h3>
        ) : heading[1].length === 2 ? (
          <h4 key={key++}>{content}</h4>
        ) : (
          <h5 key={key++}>{content}</h5>
        ),
      )
      i += 1
      continue
    }

    if (TABLE_ROW.test(line)) {
      const rows: string[][] = []
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        const cells = lines[i]
          .trim()
          .replace(/^\||\|$/g, '')
          .split('|')
          .map((cell) => cell.trim())
        if (!cells.every((cell) => SEPARATOR_CELL.test(cell))) rows.push(cells)
        i += 1
      }
      blocks.push(
        <table key={key++}>
          <thead>
            <tr>
              {rows[0]?.map((cell, index) => <th key={index}>{inline(cell)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.slice(1).map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, index) => (
                  <td key={index}>{inline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }

    if (LIST_ITEM.test(line)) {
      const items: string[] = []
      while (i < lines.length && LIST_ITEM.test(lines[i])) {
        items.push(lines[i].replace(LIST_ITEM, ''))
        i += 1
      }
      blocks.push(
        <ul key={key++}>
          {items.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </ul>,
      )
      continue
    }

    const paragraph = [line]
    i += 1
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^#{1,4}\s/.test(lines[i]) &&
      !TABLE_ROW.test(lines[i]) &&
      !LIST_ITEM.test(lines[i])
    ) {
      paragraph.push(lines[i])
      i += 1
    }
    blocks.push(<p key={key++}>{inline(paragraph.join(' '))}</p>)
  }

  return <div className="markdown-body">{blocks}</div>
}
