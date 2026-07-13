interface JsonViewerProps {
  data: unknown
  label?: string
  defaultOpen?: boolean
}

export function JsonViewer({ data, label = 'JSON', defaultOpen = false }: JsonViewerProps) {
  let text: string
  try {
    text = JSON.stringify(data, null, 2)
  } catch {
    text = String(data)
  }
  if (text === undefined) text = 'undefined'
  return (
    <details className="json-viewer" open={defaultOpen}>
      <summary>{label}</summary>
      <pre>{text}</pre>
    </details>
  )
}
