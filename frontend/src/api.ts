import type {
  HeaderConfig,
  HeaderSymbol,
  HeaderWsEvent,
  Settings,
  TextTranslateDirection,
  TextWsEvent,
  WsEvent,
} from './types'

let _port = 8765

export function setApiPort(port: number) { _port = port }
export const apiBase = () => `http://127.0.0.1:${_port}`
export const wsBase  = () => `ws://127.0.0.1:${_port}`

// ── REST helpers ──────────────────────────────────────────────────────────────

export async function apiHealth(): Promise<boolean> {
  try {
    const r = await fetch(`${apiBase()}/api/health`)
    return r.ok
  } catch { return false }
}

export async function apiScan(paths: string[], extensions?: string[]): Promise<{ files: string[]; errors: string[] }> {
  const r = await fetch(`${apiBase()}/api/scan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths, ...(extensions ? { extensions } : {}) }),
  })
  return r.json()
}

export async function apiCheck(host: string, model: string): Promise<{ ok: boolean; message: string }> {
  const r = await fetch(`${apiBase()}/api/check?host=${encodeURIComponent(host)}&model=${encodeURIComponent(model)}`)
  return r.json()
}

export async function apiModels(host: string): Promise<{ ok: boolean; models: string[]; message: string }> {
  const r = await fetch(`${apiBase()}/api/models?host=${encodeURIComponent(host)}`)
  return r.json()
}

export async function apiComments(paths: string[]): Promise<Record<string, any[]>> {
  const r = await fetch(`${apiBase()}/api/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  })
  const data = await r.json()
  return data.comments ?? {}
}

export async function apiApply(translations: Record<string, Record<string, string>>): Promise<{ applied: string[]; errors: string[] }> {
  const r = await fetch(`${apiBase()}/api/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ translations }),
  })
  return r.json()
}

export async function apiAnalyzeHeader(path: string): Promise<{ symbols: HeaderSymbol[]; error?: string }> {
  const r = await fetch(`${apiBase()}/api/analyze-header`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  return r.json()
}

export async function apiPreviewComments(
  path: string,
  comments: Record<string, string>,
  replaceExisting: boolean,
): Promise<{ ok: boolean; diff: string; preview: string; error?: string }> {
  const r = await fetch(`${apiBase()}/api/preview-comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, comments, replace_existing: replaceExisting }),
  })
  return r.json()
}

export async function apiApplyComments(
  path: string,
  comments: Record<string, string>,
  replaceExisting: boolean,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${apiBase()}/api/apply-comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, comments, replace_existing: replaceExisting }),
  })
  return r.json()
}

// ── WebSocket: file translation stream ───────────────────────────────────────

export function startTranslation(
  settings: Settings,
  paths: string[],
  onEvent: (e: WsEvent) => void,
): { stop: () => void } {
  const ws = new WebSocket(`${wsBase()}/ws`)

  ws.onopen = () => {
    ws.send(JSON.stringify({
      paths,
      host: settings.host,
      model: settings.model,
      chunk_threshold: 120,
    }))
  }

  ws.onmessage = (msg) => {
    try { onEvent(JSON.parse(msg.data) as WsEvent) } catch { /* ignore */ }
  }

  ws.onerror = () => onEvent({ type: 'error', message: 'WebSocket 连接失败' })

  return { stop: () => { if (ws.readyState === WebSocket.OPEN) ws.close() } }
}

// ── WebSocket: text translation stream ───────────────────────────────────────

function startEnglishToChineseTextTranslation(
  settings: Settings,
  text: string,
  onEvent: (e: TextWsEvent) => void,
): { stop: () => void } {
  const ws = new WebSocket(`${wsBase()}/ws/translate-text`)

  ws.onopen = () => {
    ws.send(JSON.stringify({
      text,
      host: settings.host,
      model: settings.model,
      chunk_threshold: 120,
    }))
  }

  ws.onmessage = (msg) => {
    try { onEvent(JSON.parse(msg.data) as TextWsEvent) } catch { /* ignore */ }
  }

  ws.onerror = () => onEvent({ type: 'error', message: 'WebSocket 连接失败' })

  return { stop: () => { if (ws.readyState === WebSocket.OPEN) ws.close() } }
}

export function startTextTranslation(
  settings: Settings,
  text: string,
  direction: TextTranslateDirection,
  onEvent: (e: TextWsEvent) => void,
): { stop: () => void } {
  if (direction === 'en_to_zh') {
    return startEnglishToChineseTextTranslation(settings, text, onEvent)
  }

  const w = window as any
  if (!w.electronAPI?.translateText) {
    queueMicrotask(() => {
      onEvent({ type: 'error', message: '当前环境暂不支持中文转英文，请在 Electron 应用中使用。' })
    })
    return { stop: () => {} }
  }

  let stopped = false

  void w.electronAPI.translateText({
    host: settings.host,
    model: settings.model,
    text,
    sourceLanguage: 'Chinese',
    targetLanguage: 'English',
  }).then((translated: string) => {
    if (stopped) return
    onEvent({ type: 'chunk', idx: 0, total: 1, partial: translated })
    onEvent({ type: 'done', text: translated })
  }).catch((err: unknown) => {
    if (stopped) return
    const message = err instanceof Error ? err.message : String(err)
    onEvent({ type: 'error', message })
  })

  return { stop: () => { stopped = true } }
}

// ── WebSocket: header comment generation stream ───────────────────────────────

export function startHeaderGeneration(
  settings: Settings,
  path: string,
  symbolLines: number[],
  config: HeaderConfig,
  onEvent: (e: HeaderWsEvent) => void,
): { stop: () => void } {
  const ws = new WebSocket(`${wsBase()}/ws/generate-comments`)

  ws.onopen = () => {
    ws.send(JSON.stringify({
      path,
      symbol_lines: symbolLines,
      replace_existing: config.replaceExisting,
      host: settings.host,
      model: settings.model,
      include_brief: config.includeBrief,
      include_params: config.includeParams,
      include_return: config.includeReturn,
      author: config.author,
      include_date: config.includeDate,
      date_format: config.dateFormat,
      custom_tags: config.customTags,
      language: config.language ?? 'zh',
    }))
  }

  ws.onmessage = (msg) => {
    try { onEvent(JSON.parse(msg.data) as HeaderWsEvent) } catch { /* ignore */ }
  }

  ws.onerror = () => onEvent({ type: 'error', message: 'WebSocket 连接失败' })

  return { stop: () => { if (ws.readyState === WebSocket.OPEN) ws.close() } }
}
