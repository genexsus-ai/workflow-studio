import { useEffect, useRef, useState } from 'react'

export interface ComboOption {
  value: string
  label?: string
  description?: string
}

interface ComboboxProps {
  value: string
  options: ComboOption[]
  placeholder: string
  emptyText?: string
  onChange: (value: string) => void
}

/** Filter-as-you-type replacement for <select> — stays usable when the
 * option list grows to hundreds of entries. */
export function Combobox({ value, options, placeholder, emptyText, onChange }: ComboboxProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDocClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const selected = options.find((option) => option.value === value)
  const needle = query.trim().toLowerCase()
  const filtered = needle
    ? options.filter((option) =>
        `${option.value} ${option.label ?? ''} ${option.description ?? ''}`
          .toLowerCase()
          .includes(needle),
      )
    : options

  const pick = (next: string) => {
    onChange(next)
    setOpen(false)
    setQuery('')
  }

  if (options.length === 0) {
    return <div className="combobox-empty">{emptyText ?? 'No options available'}</div>
  }

  return (
    <div className="combobox" ref={rootRef}>
      <input
        value={open ? query : (selected?.label ?? value)}
        placeholder={open ? 'Type to filter…' : placeholder}
        onFocus={() => {
          setOpen(true)
          setQuery('')
        }}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false)
          if (event.key === 'Enter' && filtered.length > 0) {
            event.preventDefault()
            pick(filtered[0].value)
          }
        }}
      />
      {open && (
        <ul className="combobox-list">
          {filtered.map((option) => (
            <li
              key={option.value}
              className={option.value === value ? 'active' : undefined}
              onMouseDown={(event) => {
                event.preventDefault()
                pick(option.value)
              }}
            >
              <span className="combobox-label">{option.label ?? option.value}</span>
              {option.description && (
                <span className="combobox-description">{option.description}</span>
              )}
            </li>
          ))}
          {filtered.length === 0 && <li className="combobox-none">No matches</li>}
        </ul>
      )}
    </div>
  )
}
