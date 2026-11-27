// Presentation-only text normalisation. Stored values stay exactly as crawled, so search
// still matches the raw string; this module only shapes what a reader sees.
// Titles and company names arrive as the platform typed them: lowercase, no space before "(",
// acronyms in lower case. The dashboard is a report, so presentation is normalised here — the
// stored value stays exactly as crawled and search still matches on the raw string.
const ACRONYMS = new Set([
  'qc', 'qa', 'it', 'ba', 'sql', 'aws', 'seo', 'ui', 'ux', 'api', 'ai', 'ci', 'cd',
  'erp', 'crm', 'php', 'css', 'html', 'sap', 'bi', 'etl', 'vps', 'gcp', 'ios', 'c++', 'c#',
])

function prettyText(value: string | null | undefined): string {
  if (!value) return ''
  let text = String(value).replace(/\s+/g, ' ').trim()
  if (!text) return ''
  // "analyst(ecommerce)" -> "analyst (ecommerce)"
  text = text.replace(/([^\s(])\(/g, '$1 (')
  // Sentence case on the first letter, and on the first letter inside parentheses.
  text = text.replace(/^([\s"'`\[]*)([a-zà-ỹ])/u, (_m, prefix, ch) => prefix + ch.toUpperCase())
  text = text.replace(/\(([a-zà-ỹ])/gu, (_m, ch) => '(' + ch.toUpperCase())
  // Known technical acronyms are shouted, the rest of the sentence keeps its case.
  text = text.split(' ').map((word) => {
    const bare = word.replace(/[^\p{L}\p{N}+#]/gu, '')
    return ACRONYMS.has(bare.toLowerCase()) ? word.replace(bare, bare.toUpperCase()) : word
  }).join(' ')
  return text
}

function prettyCompany(value: string | null | undefined): string {
  const text = prettyText(value)
  if (!text) return 'Chưa xác định'
  // "CELLPHONES" / "cellphones" / "CellphoneS" all read as a name: keep the author's inner caps
  // when they exist, otherwise lift the first letter only.
  return /[A-Z]/.test(text.slice(1)) ? text : text.charAt(0).toUpperCase() + text.slice(1)
}

export { prettyText, prettyCompany }
