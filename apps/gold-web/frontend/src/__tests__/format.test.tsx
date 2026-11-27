// Titles and company names reach the dashboard exactly as the source typed them: lowercase, no
// space before "(", acronyms in lower case. The page is a report, so presentation is normalised
// here — the stored value stays untouched and search still matches the raw string.
import { describe, expect, it } from 'vitest'
import { prettyCompany, prettyText } from '../format'

describe('prettyText', () => {
  it('capitalises the first letter of a lowercase title', () => {
    expect(prettyText('trợ lý dự án công nghệ thông tin')).toBe('Trợ lý dự án công nghệ thông tin')
  })

  it('adds the missing space before a parenthesis', () => {
    expect(prettyText('business analyst(ecommerce/retail)')).toBe('Business analyst (Ecommerce/retail)')
  })

  it('keeps known technical acronyms shouted', () => {
    // the parenthetical takes a capital too, so the line reads as two sentences, not a stray tag
    expect(prettyText('thực tập sinh qc(travel tech)')).toBe('Thực tập sinh QC (Travel tech)')
    // only the acronym is shouted; ordinary words keep the source's case (we are not title-casing)
    expect(prettyText('kỹ sư sql và python')).toBe('Kỹ sư SQL và python')
  })

  it('leaves an already correct title alone', () => {
    expect(prettyText('Senior Backend Engineer')).toBe('Senior Backend Engineer')
  })

  it('collapses stray whitespace and empty input', () => {
    expect(prettyText('  data   engineer  ')).toBe('Data engineer')
    expect(prettyText('')).toBe('')
    expect(prettyText(null)).toBe('')
  })

  it('does not lowercase anything the source capitalised', () => {
    expect(prettyText('Kỹ sư DevOps (Hà Nội)')).toBe('Kỹ sư DevOps (Hà Nội)')
  })
})

describe('prettyCompany', () => {
  it('capitalises a lowercase brand', () => {
    expect(prettyCompany('cellphones')).toBe('Cellphones')
    expect(prettyCompany('bizitrip')).toBe('Bizitrip')
  })

  it('respects inner capitals the source already has', () => {
    expect(prettyCompany('CellphoneS')).toBe('CellphoneS')
    expect(prettyCompany('FPT Software')).toBe('FPT Software')
  })

  it('never prints an empty company', () => {
    expect(prettyCompany(null)).toBe('Chưa xác định')
    expect(prettyCompany('   ')).toBe('Chưa xác định')
  })
})
