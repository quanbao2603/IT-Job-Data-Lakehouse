/** Markdown renderer contract: business formatting works, untrusted input does not execute. */
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from '../Markdown'

describe('Markdown', () => {
  it('renders bold and per-line metric lists', () => {
    const { container } = render(<Markdown text={'**Theo chức danh**\n- Lương trung vị: **31,25 triệu**\n- Số tin: **14 tin**'} />)
    expect(container.querySelectorAll('strong').length).toBe(3)
    expect(container.querySelectorAll('ul li').length).toBe(2)
  })

  it('renders a markdown table as a real table', () => {
    const { container } = render(<Markdown text={'| Nhóm | Số tin |\n| --- | --- |\n| Junior | 158 |'} />)
    const table = container.querySelector('table')
    expect(table).toBeTruthy()
    expect(table?.querySelectorAll('tbody tr').length).toBe(1)
    expect(container.textContent).toContain('Junior')
  })

  it('turns bare URLs into links but never javascript:', () => {
    const { container } = render(<Markdown text={'Đường dẫn: https://example.com/jobs/1.'} />)
    const link = container.querySelector('a')
    expect(link?.getAttribute('href')).toBe('https://example.com/jobs/1')

    const unsafe = render(<Markdown text={'[bấm](javascript:alert(1))'} />)
    expect(unsafe.container.querySelector('a')).toBeNull()
  })

  it('never renders raw HTML from the model', () => {
    const { container } = render(<Markdown text={'<img src=x onerror=alert(1)> **ok**'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
  })

  it('drops fenced code blocks', () => {
    const { container } = render(<Markdown text={'Số tin: **12**\n```sql\nSELECT 1\n```'} />)
    expect(container.textContent).not.toContain('SELECT')
    expect(container.textContent).toContain('12')
  })
})
