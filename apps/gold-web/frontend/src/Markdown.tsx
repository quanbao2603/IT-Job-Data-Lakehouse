/**
 * Minimal, safe Markdown renderer for the assistant answer.
 *
 * The answer is model output, so it is treated as untrusted data: no raw HTML is ever
 * injected, links are restricted to http(s), and fenced code is dropped (a code block in a
 * business answer is a leak, not content). Tables and bold/lists are rendered as real DOM
 * so a metric list reads as lines of a row, not as one long paragraph.
 */
import type { ReactNode } from 'react'

type InlineOptions = { bold?: boolean }

function safeHref(raw: string): string | null {
  const href = raw.trim()
  if (!/^https?:\/\//i.test(href)) return null
  try {
    const parsed = new URL(href)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.toString() : null
  } catch {
    return null
  }
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  // Order matters: link, bold, inline code, italic.
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*\n]+)\*|(https?:\/\/[^\s<>()"']+)/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let index = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const key = `${keyPrefix}-${index++}`
    if (match[1] && match[2]) {
      const href = safeHref(match[2])
      nodes.push(href
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{match[1]}</a>
        : <span key={key}>{match[1]}</span>)
    } else if (match[3] !== undefined) {
      nodes.push(<strong key={key}>{renderInline(match[3], key)}</strong>)
    } else if (match[4] !== undefined) {
      nodes.push(<span className="md-inline-code" key={key}>{match[4]}</span>)
    } else if (match[5] !== undefined) {
      nodes.push(<em key={key}>{renderInline(match[5], key)}</em>)
    } else if (match[6] !== undefined) {
      // Bare URL written by the model: still a real link, but only http(s).
      const trimmed = match[6].replace(/[.,;:]+$/, '')
      const href = safeHref(trimmed)
      nodes.push(href
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{trimmed}</a>
        : <span key={key}>{trimmed}</span>)
      const tail = match[6].slice(trimmed.length)
      if (tail) nodes.push(tail)
    }
    lastIndex = pattern.lastIndex
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

function splitRow(line: string): string[] {
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((cell) => cell.trim())
}

const isSeparator = (line: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-')

export function Markdown({ text, options }: { text: string; options?: InlineOptions }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let index = 0
  let key = 0

  while (index < lines.length) {
    const line = lines[index] ?? ''
    const trimmed = line.trim()

    // Fenced code is never rendered.
    if (trimmed.startsWith('```')) {
      index += 1
      while (index < lines.length && !(lines[index] ?? '').trim().startsWith('```')) index += 1
      index += 1
      continue
    }

    if (!trimmed) {
      index += 1
      continue
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      const level = heading[1]?.length ?? 3
      const content = renderInline(heading[2] ?? '', `h${key}`)
      blocks.push(level <= 2
        ? <h3 className="md-heading" key={`h${key++}`}>{content}</h3>
        : <h4 className="md-subheading" key={`h${key++}`}>{content}</h4>)
      index += 1
      continue
    }

    // Table: header row followed by a separator row.
    if (trimmed.includes('|') && isSeparator(lines[index + 1] ?? '')) {
      const header = splitRow(trimmed)
      const rows: string[][] = []
      index += 2
      while (index < lines.length && (lines[index] ?? '').trim().includes('|')) {
        rows.push(splitRow((lines[index] ?? '').trim()))
        index += 1
      }
      blocks.push(
        <div className="md-table-wrap" key={`t${key++}`}>
          <table className="md-table">
            <thead><tr>{header.map((cell, cellIndex) => <th key={cellIndex}>{renderInline(cell, `th${cellIndex}`)}</th>)}</tr></thead>
            <tbody>
              {rows.map((row, rowIndex) => <tr key={rowIndex}>
                {row.map((cell, cellIndex) => <td key={cellIndex}>{renderInline(cell, `td${rowIndex}-${cellIndex}`)}</td>)}
              </tr>)}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const bullet = line.match(/^(\s*)[-*•]\s+(.*)$/)
    if (bullet) {
      const items: string[] = []
      while (index < lines.length) {
        const current = lines[index] ?? ''
        const match = current.match(/^\s*[-*•]\s+(.*)$/)
        if (!match) break
        items.push(match[1] ?? '')
        index += 1
      }
      blocks.push(
        <ul className="md-list" key={`u${key++}`}>
          {items.map((item, itemIndex) => <li key={itemIndex}>{renderInline(item, `li${itemIndex}`)}</li>)}
        </ul>,
      )
      continue
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (ordered) {
      const items: string[] = []
      while (index < lines.length) {
        const match = (lines[index] ?? '').match(/^\s*\d+[.)]\s+(.*)$/)
        if (!match) break
        items.push(match[1] ?? '')
        index += 1
      }
      blocks.push(
        <ol className="md-list" key={`o${key++}`}>
          {items.map((item, itemIndex) => <li key={itemIndex}>{renderInline(item, `oli${itemIndex}`)}</li>)}
        </ol>,
      )
      continue
    }

    // Paragraph: consume until a blank line or another block starts.
    const paragraph: string[] = []
    while (index < lines.length) {
      const current = lines[index] ?? ''
      const stop = !current.trim()
        || /^\s*[-*•]\s+/.test(current)
        || /^\s*\d+[.)]\s+/.test(current)
        || current.trim().startsWith('```')
        || /^(#{1,4})\s+/.test(current.trim())
        || (current.trim().includes('|') && isSeparator(lines[index + 1] ?? ''))
      if (stop && paragraph.length > 0) break
      if (stop) break
      paragraph.push(current.trim())
      index += 1
    }
    if (paragraph.length === 0) {
      index += 1
      continue
    }
    blocks.push(
      <p className="md-paragraph" key={`p${key++}`}>
        {paragraph.map((part, partIndex) => (
          <span key={partIndex}>
            {partIndex > 0 && ' '}
            {renderInline(part, `pi${key}-${partIndex}`)}
          </span>
        ))}
      </p>,
    )
  }

  return <div className={options?.bold ? 'md md-emphasis' : 'md'}>{blocks}</div>
}
