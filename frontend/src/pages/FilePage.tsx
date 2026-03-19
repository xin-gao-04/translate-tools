import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { CommentRow, FileEntry, FileFilterOption, Settings, WsEvent } from '../types'
import { apiApply, apiComments, apiScan, startTranslation } from '../api'
import FilePanel from '../components/FilePanel'
import CommentTable from '../components/CommentTable'
import BottomBar from '../components/BottomBar'

// ── State ─────────────────────────────────────────────────────────────────────

interface State {
  files: FileEntry[]
  comments: Record<string, CommentRow[]>
  commentsLoaded: Record<string, boolean>
  translations: Record<string, Record<string, string>>
  isRunning: boolean
  doneFiles: number
  totalTranslated: number
  statusMsg: string
}

type Action =
  | { type: 'ADD_FILES';       paths: string[] }
  | { type: 'CLEAR_FILES' }
  | { type: 'TOGGLE_FILE_SELECT'; path: string }
  | { type: 'SELECT_ALL_FILES' }
  | { type: 'SELECT_NONE_FILES' }
  | { type: 'LOAD_COMMENTS';   path: string; rows: CommentRow[] }
  | { type: 'FILE_STARTED';    path: string; total: number }
  | { type: 'FILE_DONE';       path: string; translated: number; untranslated: number; total: number; trans: Record<string, string> }
  | { type: 'FILE_ERROR';      path: string; message: string }
  | { type: 'COMMENT_RUNNING'; path: string; lineno: number; chunkTotal: number }
  | { type: 'COMMENT_CHUNK';   path: string; lineno: number; partial: string }
  | { type: 'COMMENT_DONE';    path: string; lineno: number; translated: string }
  | { type: 'COMMENT_FAILED';  path: string; lineno: number; message: string }
  | { type: 'START_RUNNING' }
  | { type: 'ALL_DONE';        translated: number; errors: number }
  | { type: 'STATUS';          msg: string }
  | { type: 'TRANSLATIONS_APPLIED'; paths: string[] }

const basename = (p: string) => p.replace(/\\/g, '/').split('/').pop() ?? p
const FILE_FILTERS: FileFilterOption[] = [
  { key: 'cpp_src', label: 'C/C++ 源文件', extensions: ['.cpp', '.cxx', '.cc', '.c'] },
  { key: 'cpp_header', label: 'C/C++ 头文件', extensions: ['.h', '.hpp', '.hxx', '.hh', '.inl', '.ipp'] },
  { key: 'cmake', label: 'CMake', extensions: ['.cmake', 'CMakeLists.txt'] },
]

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'ADD_FILES': {
      const existing = new Set(state.files.map(f => f.path))
      const newFiles: FileEntry[] = action.paths
        .filter(p => !existing.has(p))
        .map(p => ({ path: p, name: basename(p), status: 'pending', translated: 0, total: 0, selected: true }))
      return { ...state, files: [...state.files, ...newFiles] }
    }
    case 'CLEAR_FILES':
      return { ...state, files: [], comments: {}, commentsLoaded: {}, translations: {} }
    case 'TOGGLE_FILE_SELECT':
      return {
        ...state,
        files: state.files.map(f => f.path === action.path ? { ...f, selected: !f.selected } : f),
      }
    case 'SELECT_ALL_FILES':
      return { ...state, files: state.files.map(f => ({ ...f, selected: true })) }
    case 'SELECT_NONE_FILES':
      return { ...state, files: state.files.map(f => ({ ...f, selected: false })) }

    case 'LOAD_COMMENTS':
      return {
        ...state,
        comments: { ...state.comments, [action.path]: action.rows },
        commentsLoaded: { ...state.commentsLoaded, [action.path]: true },
      }

    case 'FILE_STARTED':
      return {
        ...state,
        files: state.files.map(f =>
          f.path === action.path ? { ...f, status: 'running', total: action.total, untranslated: 0 } : f
        ),
      }

    case 'FILE_DONE': {
      const newTrans = { ...state.translations, [action.path]: action.trans }
      return {
        ...state,
        doneFiles: state.doneFiles + 1,
        totalTranslated: state.totalTranslated + action.translated,
        translations: newTrans,
        files: state.files.map(f =>
          f.path === action.path
            ? {
                ...f,
                status: action.untranslated > 0 ? 'error' : 'done',
                translated: action.translated,
                total: action.total,
                untranslated: action.untranslated,
              }
            : f
        ),
      }
    }
    case 'FILE_ERROR':
      return {
        ...state,
        doneFiles: state.doneFiles + 1,
        files: state.files.map(f => f.path === action.path ? { ...f, status: 'error' } : f),
      }

    case 'COMMENT_RUNNING': {
      // Guard: if file hasn't been loaded yet, don't create a spurious empty array.
      // COMMENT_RUNNING/CHUNK/DONE events can arrive before the user ever clicks
      // the file; ?? [] would set comments[path] = [] (an empty array), which is
      // truthy and would cause the table to render empty instead of null.
      if (!(action.path in state.comments)) return state
      return {
        ...state,
        comments: {
          ...state.comments,
          [action.path]: state.comments[action.path].map(r =>
            r.lineno === action.lineno ? { ...r, status: 'running', chunkTotal: action.chunkTotal, translated: '' } : r
          ),
        },
      }
    }
    case 'COMMENT_CHUNK': {
      if (!(action.path in state.comments)) return state
      return {
        ...state,
        comments: {
          ...state.comments,
          [action.path]: state.comments[action.path].map(r =>
            r.lineno === action.lineno ? { ...r, translated: action.partial } : r
          ),
        },
      }
    }
    case 'COMMENT_DONE': {
      if (!(action.path in state.comments)) return state
      return {
        ...state,
        comments: {
          ...state.comments,
          [action.path]: state.comments[action.path].map(r =>
            r.lineno === action.lineno ? { ...r, status: 'done', translated: action.translated } : r
          ),
        },
      }
    }
    case 'COMMENT_FAILED': {
      if (!(action.path in state.comments)) return state
      return {
        ...state,
        comments: {
          ...state.comments,
          [action.path]: state.comments[action.path].map(r =>
            r.lineno === action.lineno ? { ...r, status: 'error', translated: action.message } : r
          ),
        },
      }
    }

    case 'START_RUNNING':
      return { ...state, isRunning: true, doneFiles: 0, totalTranslated: 0 }

    case 'ALL_DONE':
      return { ...state, isRunning: false }

    case 'STATUS':
      return { ...state, statusMsg: action.msg }

    case 'TRANSLATIONS_APPLIED':
      return {
        ...state,
        translations: Object.fromEntries(
          Object.entries(state.translations).filter(([path]) => !action.paths.includes(path))
        ),
      }

    default:
      return state
  }
}

const INIT: State = {
  files: [], comments: {}, commentsLoaded: {}, translations: {},
  isRunning: false, doneFiles: 0, totalTranslated: 0,
  statusMsg: '就绪 — 拖放文件夹或点击「添加」开始',
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props { settings: Settings }

export default function FilePage({ settings }: Props) {
  const [state, dispatch] = useReducer(reducer, INIT)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [activeFilters, setActiveFilters] = useState<string[]>(FILE_FILTERS.map(item => item.key))
  const wsRef = useRef<{ stop: () => void } | null>(null)

  // Keep a stable ref to settings so handleWsEvent can read it without
  // needing to be recreated every time settings change.
  const settingsRef = useRef(settings)
  useEffect(() => { settingsRef.current = settings }, [settings])

  const handleWsEvent = useCallback((evt: WsEvent) => {
    switch (evt.type) {
      case 'file_started':
        dispatch({ type: 'FILE_STARTED', path: evt.path, total: evt.english_count })
        setSelectedFile(evt.path)
        dispatch({ type: 'STATUS', msg: `翻译中: ${basename(evt.path)}  (${evt.english_count} 条英文注释)` })
        break
      case 'comment_started':
        dispatch({ type: 'COMMENT_RUNNING', path: evt.path, lineno: evt.lineno, chunkTotal: evt.chunk_total })
        break
      case 'comment_chunk':
        dispatch({ type: 'COMMENT_CHUNK', path: evt.path, lineno: evt.lineno, partial: evt.partial })
        break
      case 'comment_done':
        dispatch({ type: 'COMMENT_DONE', path: evt.path, lineno: evt.lineno, translated: evt.translated })
        break
      case 'comment_failed':
        dispatch({ type: 'COMMENT_FAILED', path: evt.path, lineno: evt.lineno, message: evt.message })
        dispatch({ type: 'STATUS', msg: `未翻译: ${basename(evt.path)} L${evt.lineno} — ${evt.message}` })
        break
      case 'file_done': {
        const trans = evt.translations ?? {}
        dispatch({
          type: 'FILE_DONE',
          path: evt.path,
          translated: evt.translated,
          untranslated: evt.untranslated,
          total: evt.translated + evt.untranslated,
          trans,
        })
        // In inplace mode, auto-write translations to disk immediately
        if (
          settingsRef.current.outputMode === 'inplace' &&
          evt.untranslated === 0 &&
          Object.keys(trans).length > 0
        ) {
          dispatch({ type: 'STATUS', msg: `写回文件: ${basename(evt.path)}…` })
          apiApply({ [evt.path]: trans }).then(({ applied, errors }) => {
            if (errors.length) {
              dispatch({ type: 'STATUS', msg: `写回失败: ${errors.join(', ')}` })
            } else if (applied.length) {
              dispatch({ type: 'TRANSLATIONS_APPLIED', paths: applied })
              dispatch({ type: 'STATUS', msg: `✓ 已写回: ${basename(evt.path)}` })
            }
          })
        }
        break
      }
      case 'file_error':
        dispatch({ type: 'FILE_ERROR', path: evt.path, message: evt.message })
        dispatch({ type: 'STATUS', msg: `错误: ${basename(evt.path)}: ${evt.message}` })
        break
      case 'all_done':
        dispatch({ type: 'ALL_DONE', translated: evt.translated, errors: evt.errors })
        dispatch({
          type: 'STATUS',
          msg: `✓ 完成 — ${evt.translated} 条注释已翻译（${evt.files} 个文件）${evt.errors ? `，${evt.errors} 个错误` : ''}`,
        })
        wsRef.current = null
        break
      case 'log':
        dispatch({ type: 'STATUS', msg: evt.message })
        break
    }
  }, [])

  const handleAddPaths = useCallback(async (paths: string[]) => {
    dispatch({ type: 'STATUS', msg: '扫描文件…' })
    const extensions = FILE_FILTERS
      .filter(item => activeFilters.includes(item.key))
      .flatMap(item => item.extensions)
    const { files, errors } = await apiScan(paths, extensions)
    if (errors.length) console.warn('scan errors:', errors)
    dispatch({ type: 'ADD_FILES', paths: files })
    dispatch({ type: 'STATUS', msg: `已添加 ${files.length} 个文件` })
  }, [activeFilters])

  const handleSelectFile = useCallback(async (path: string) => {
    setSelectedFile(path)
    if (!state.commentsLoaded[path]) {
      const data = await apiComments([path])
      const rawRows = data[path] ?? []
      const rows: CommentRow[] = rawRows.map((r: any) => {
        const cachedTr = state.translations[path]?.[String(r.line_start)]
        return {
          lineno:        r.line_start,
          lineEnd:       r.line_end,
          style:         r.style,
          original:      r.text,
          translated:    cachedTr ?? '',
          isEnglish:     r.is_english,
          status:        cachedTr ? 'done' : r.is_english ? 'pending' : 'skipped',
          chunkTotal:    1,
          contextBefore: r.context_before ?? [],
          contextAfter:  r.context_after  ?? [],
        }
      })
      dispatch({ type: 'LOAD_COMMENTS', path, rows })
    }
  }, [state.commentsLoaded, state.translations])

  const handleStart = useCallback(() => {
    if (state.isRunning || state.files.length === 0) return
    dispatch({ type: 'STATUS', msg: '连接到 Ollama…' })
    const paths = state.files.map(f => f.path)
    dispatch({ type: 'START_RUNNING' })
    const ctrl = startTranslation(settings, paths, handleWsEvent)
    wsRef.current = ctrl
    dispatch({ type: 'STATUS', msg: '翻译开始…' })
  }, [state.isRunning, state.files, settings, handleWsEvent])

  const handleStop = useCallback(() => {
    wsRef.current?.stop()
    wsRef.current = null
    dispatch({ type: 'ALL_DONE', translated: 0, errors: 0 })
    dispatch({ type: 'STATUS', msg: '已停止' })
  }, [])

  const handleApply = useCallback(async () => {
    const selectedPaths = state.files
      .filter(f => f.selected && state.translations[f.path] && Object.keys(state.translations[f.path]).length > 0)
      .map(f => f.path)

    if (selectedPaths.length === 0) return

    const selectedTranslations = Object.fromEntries(
      selectedPaths.map(path => [path, state.translations[path]])
    )

    dispatch({ type: 'STATUS', msg: '写回文件…' })
    const { applied, errors } = await apiApply(selectedTranslations)
    if (errors.length) {
      dispatch({ type: 'STATUS', msg: `部分失败: ${errors.join('; ')}` })
    } else {
      dispatch({ type: 'TRANSLATIONS_APPLIED', paths: applied })
      dispatch({ type: 'STATUS', msg: `✓ 已写回 ${applied.length} 个文件` })
    }
  }, [state.files, state.translations])

  const currentRows = selectedFile ? (state.comments[selectedFile] ?? null) : null
  const doneRows    = currentRows?.filter(r => r.status === 'done').length ?? 0
  const engRows     = currentRows?.filter(r => r.isEnglish).length ?? 0
  const selectedFiles = state.files.filter(f => f.selected)
  const hasBlockingErrors = selectedFiles.some(f => f.status === 'error' || (f.untranslated ?? 0) > 0)
  const selectedApplyCount = selectedFiles.filter(f => {
    const trans = state.translations[f.path]
    return trans && Object.keys(trans).length > 0
  }).length
  const hasTranslations = selectedApplyCount > 0

  return (
    <div className="page-layout">
      <div className="main-area">
        <FilePanel
          files={state.files}
          selectedFile={selectedFile}
          filters={FILE_FILTERS}
          activeFilters={activeFilters}
          onToggleFilter={key => {
            setActiveFilters(current => {
              if (current.includes(key)) {
                return current.length > 1 ? current.filter(item => item !== key) : current
              }
              return [...current, key]
            })
          }}
          onSelect={handleSelectFile}
          onToggleSelect={path => dispatch({ type: 'TOGGLE_FILE_SELECT', path })}
          onSelectAll={() => dispatch({ type: 'SELECT_ALL_FILES' })}
          onSelectNone={() => dispatch({ type: 'SELECT_NONE_FILES' })}
          onAddPaths={handleAddPaths}
          onClear={() => dispatch({ type: 'CLEAR_FILES' })}
        />
        <div className="content">
          <div className="content-header">
            {selectedFile ?? '— 在左侧点击文件查看注释 —'}
          </div>
          <CommentTable rows={currentRows} />
          <div className="file-progress-bar">
            <label>进度</label>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: engRows ? `${(doneRows / engRows) * 100}%` : '0%' }}
              />
            </div>
            <span>{doneRows} / {engRows}</span>
          </div>
        </div>
      </div>
      <BottomBar
        files={state.files.length}
        doneFiles={state.doneFiles}
        totalTranslated={state.totalTranslated}
        selectedApplyCount={selectedApplyCount}
        statusMsg={state.statusMsg}
        isRunning={state.isRunning}
        canApply={hasTranslations && !hasBlockingErrors && settings.outputMode !== 'inplace'}
        onStart={handleStart}
        onStop={handleStop}
        onApply={handleApply}
      />
    </div>
  )
}
