export type FileStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'

export interface FileEntry {
  path: string
  name: string
  status: FileStatus
  translated: number
  total: number
}

export interface CommentRow {
  lineno: number
  lineEnd: number
  style: string
  original: string
  translated: string
  isEnglish: boolean
  status: 'pending' | 'running' | 'done' | 'skipped'
  chunkTotal: number
  contextBefore: string[]   // source lines before the comment
  contextAfter: string[]    // source lines after the comment
}

export type WsEvent =
  | { type: 'file_started';    path: string; english_count: number; total_comments: number }
  | { type: 'comment_started'; path: string; lineno: number; chunk_total: number }
  | { type: 'comment_chunk';   path: string; lineno: number; partial: string; chunk_idx: number; chunk_total: number }
  | { type: 'comment_done';    path: string; lineno: number; translated: string }
  | { type: 'file_done';       path: string; translated: number; skipped: number; translations: Record<string,string> }
  | { type: 'file_error';      path: string; message: string }
  | { type: 'all_done';        files: number; translated: number; errors: number }
  | { type: 'log';             message: string }
  | { type: 'error';           message: string }

export type TextWsEvent =
  | { type: 'chunk'; idx: number; total: number; partial: string }
  | { type: 'done';  text: string }
  | { type: 'error'; message: string }

export type TextTranslateDirection = 'en_to_zh' | 'zh_to_en'

export type HeaderWsEvent =
  | { type: 'function_started'; name: string; line: number }
  | { type: 'comment_chunk';    name: string; partial: string }
  | { type: 'comment_done';     name: string; line: number; comment: string }
  | { type: 'all_done';         count: number }
  | { type: 'error';            message: string }

export interface FunctionInfo {
  name: string
  full_signature: string
  line_start: number
  line_end: number
  class_context: string
  has_comment: boolean
  existing_comment: string
  comment_line_start: number
  comment_line_end: number
  // UI state (added locally, not from server)
  selected?: boolean
  generated_comment?: string
  generate_status?: 'idle' | 'running' | 'done' | 'error'
  generate_partial?: string
}

export interface Settings {
  host: string
  model: string
  outputMode: 'inplace' | 'stdout' | 'diff'
  apiPort: number
}
