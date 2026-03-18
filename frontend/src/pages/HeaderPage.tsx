import { useCallback, useRef, useState } from 'react'
import type { HeaderConfig, HeaderSymbol, HeaderWsEvent, Settings } from '../types'
import {
  apiAnalyzeHeader,
  apiApplyComments,
  apiPreviewComments,
  apiScan,
  startHeaderGeneration,
} from '../api'

interface Props { settings: Settings }

type HeaderFileStatus = 'pending' | 'running' | 'done' | 'error'

interface HeaderFileEntry {
  path: string
  name: string
  status: HeaderFileStatus
  symbolCount: number
  generatedCount: number
}

const HEADER_EXT_RE = /\.(h|hpp|hxx|hh|inl|ipp)$/i
const FILE_ICONS: Record<HeaderFileStatus, string> = {
  pending: '○',
  running: '◐',
  done: '●',
  error: '✗',
}

const DEFAULT_CONFIG: HeaderConfig = {
  replaceExisting: false,
  includeBrief: true,
  includeParams: true,
  includeReturn: true,
  author: '',
  includeDate: false,
  dateFormat: '%Y-%m-%d',
  customTags: [],
}

const basename = (p: string) => p.replace(/\\/g, '/').split('/').pop() ?? p

export default function HeaderPage({ settings }: Props) {
  const [files, setFiles] = useState<HeaderFileEntry[]>([])
  const [symbolsByPath, setSymbolsByPath] = useState<Record<string, HeaderSymbol[]>>({})
  const [diffsByPath, setDiffsByPath] = useState<Record<string, string>>({})
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState('拖放文件夹或头文件开始分析')
  const [isScanning, setIsScanning] = useState(false)
  const [generatingPath, setGeneratingPath] = useState<string | null>(null)
  const [previewingPath, setPreviewingPath] = useState<string | null>(null)
  const [expandedLine, setExpandedLine] = useState<number | null>(null)
  const [config, setConfig] = useState<HeaderConfig>(DEFAULT_CONFIG)
  const wsRef = useRef<{ stop: () => void } | null>(null)
  const dragCounter = useRef(0)
  const [dragging, setDragging] = useState(false)

  const updateFileEntry = useCallback((path: string, updater: (entry: HeaderFileEntry) => HeaderFileEntry) => {
    setFiles(prev => {
      const existing = prev.find(entry => entry.path === path)
      if (!existing) {
        return [...prev, updater({
          path,
          name: basename(path),
          status: 'pending',
          symbolCount: 0,
          generatedCount: 0,
        })]
      }
      return prev.map(entry => entry.path === path ? updater(entry) : entry)
    })
  }, [])

  const clearDiffs = useCallback(() => {
    setDiffsByPath({})
  }, [])

  const updateConfig = useCallback((partial: Partial<HeaderConfig>) => {
    setConfig(prev => ({ ...prev, ...partial }))
    clearDiffs()
  }, [clearDiffs])

  const syncFileFromSymbols = useCallback((path: string, symbols: HeaderSymbol[]) => {
    const generatedCount = symbols.filter(symbol => !!symbol.generated_comment).length
    updateFileEntry(path, entry => ({
      ...entry,
      status: generatedCount > 0 ? 'done' : 'pending',
      symbolCount: symbols.length,
      generatedCount,
    }))
  }, [updateFileEntry])

  const loadFile = useCallback(async (path: string, force = false) => {
    if (!force && symbolsByPath[path]) {
      return
    }

    updateFileEntry(path, entry => ({ ...entry, status: 'running' }))
    setStatusMsg(`分析中: ${basename(path)}`)

    const result = await apiAnalyzeHeader(path)
    if (result.error) {
      updateFileEntry(path, entry => ({ ...entry, status: 'error' }))
      setStatusMsg(`✗ ${result.error}`)
      return
    }

    const symbols = (result.symbols ?? []).map(symbol => ({
      ...symbol,
      selected: !symbol.has_comment,
      generate_status: 'idle' as const,
    }))

    setSymbolsByPath(prev => ({ ...prev, [path]: symbols }))
    syncFileFromSymbols(path, symbols)
    setStatusMsg(`已找到 ${symbols.length} 个对象（函数 + 变量）`)
  }, [symbolsByPath, syncFileFromSymbols, updateFileEntry])

  const handleSelectFile = useCallback(async (path: string) => {
    setSelectedFile(path)
    setExpandedLine(null)
    await loadFile(path)
  }, [loadFile])

  const handleAddPaths = useCallback(async (paths: string[]) => {
    setIsScanning(true)
    setStatusMsg('扫描头文件…')
    try {
      const { files: scanned, errors } = await apiScan(paths)
      const headerPaths = scanned.filter(path => HEADER_EXT_RE.test(path))
      if (errors.length) {
        console.warn('header scan errors:', errors)
      }
      if (headerPaths.length === 0) {
        setStatusMsg('未找到 .h / .hpp / .inl / .ipp 头文件')
        return
      }

      setFiles(prev => {
        const existing = new Set(prev.map(entry => entry.path))
        const added = headerPaths
          .filter(path => !existing.has(path))
          .map(path => ({
            path,
            name: basename(path),
            status: 'pending' as const,
            symbolCount: 0,
            generatedCount: 0,
          }))
        return [...prev, ...added]
      })

      const firstPath = selectedFile ?? headerPaths[0]
      setSelectedFile(firstPath)
      await loadFile(firstPath)
      setStatusMsg(`已载入 ${headerPaths.length} 个头文件`)
    } finally {
      setIsScanning(false)
    }
  }, [loadFile, selectedFile])

  const currentSymbols = selectedFile ? (symbolsByPath[selectedFile] ?? []) : []
  const currentDiff = selectedFile ? (diffsByPath[selectedFile] ?? '') : ''
  const selectedSymbols = currentSymbols.filter(symbol => symbol.selected)
  const generatedCount = currentSymbols.filter(symbol => symbol.generate_status === 'done').length
  const previewReady = !!selectedFile && !!currentDiff

  const updateCurrentSymbols = useCallback((updater: (symbols: HeaderSymbol[]) => HeaderSymbol[]) => {
    if (!selectedFile) return
    setSymbolsByPath(prev => {
      const next = updater(prev[selectedFile] ?? [])
      queueMicrotask(() => syncFileFromSymbols(selectedFile, next))
      return { ...prev, [selectedFile]: next }
    })
  }, [selectedFile, syncFileFromSymbols])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current = 0
    setDragging(false)
    const droppedPaths = Array.from(e.dataTransfer.files)
      .map(file => (file as any).path ?? '')
      .filter(Boolean)
    if (droppedPaths.length > 0) {
      void handleAddPaths(droppedPaths)
    }
  }, [handleAddPaths])

  const handleBrowseDir = async () => {
    const w = window as any
    if (w.electronAPI?.openDirectory) {
      const paths: string[] = await w.electronAPI.openDirectory()
      if (paths.length) {
        await handleAddPaths(paths)
      }
    }
  }

  const handleBrowseFile = async () => {
    const w = window as any
    if (w.electronAPI?.openFile) {
      const paths: string[] = await w.electronAPI.openFile()
      if (paths.length) {
        await handleAddPaths(paths)
      }
    }
  }

  const toggleSelect = (lineStart: number) => {
    updateCurrentSymbols(symbols => symbols.map(symbol =>
      symbol.line_start === lineStart ? { ...symbol, selected: !symbol.selected } : symbol
    ))
  }

  const selectAll = () => updateCurrentSymbols(symbols => symbols.map(symbol => ({ ...symbol, selected: true })))
  const selectNone = () => updateCurrentSymbols(symbols => symbols.map(symbol => ({ ...symbol, selected: false })))
  const selectNoComment = () => updateCurrentSymbols(symbols => symbols.map(symbol => ({
    ...symbol,
    selected: !symbol.has_comment,
  })))

  const generatedCommentsFor = (path: string) => {
    const comments: Record<string, string> = {}
    for (const symbol of symbolsByPath[path] ?? []) {
      if (symbol.generated_comment) {
        comments[String(symbol.line_start)] = symbol.generated_comment
      }
    }
    return comments
  }

  const handleGenerate = useCallback(() => {
    if (!selectedFile || generatingPath) return

    const symbols = symbolsByPath[selectedFile] ?? []
    const targetSymbols = symbols.filter(symbol =>
      symbol.selected && (config.replaceExisting || !symbol.has_comment)
    )
    if (targetSymbols.length === 0) {
      setStatusMsg('没有需要生成的对象，请检查选择项或“替换已有注释”配置')
      return
    }

    setGeneratingPath(selectedFile)
    setDiffsByPath(prev => {
      const next = { ...prev }
      delete next[selectedFile]
      return next
    })
    updateFileEntry(selectedFile, entry => ({ ...entry, status: 'running' }))
    setStatusMsg('生成注释中…')

    setSymbolsByPath(prev => ({
      ...prev,
      [selectedFile]: (prev[selectedFile] ?? []).map(symbol =>
        targetSymbols.some(target => target.line_start === symbol.line_start)
          ? {
              ...symbol,
              generate_status: 'idle',
              generated_comment: undefined,
              generate_partial: undefined,
            }
          : symbol
      ),
    }))

    const onEvent = (evt: HeaderWsEvent) => {
      switch (evt.type) {
        case 'symbol_started':
          setSymbolsByPath(prev => ({
            ...prev,
            [selectedFile]: (prev[selectedFile] ?? []).map(symbol =>
              symbol.line_start === evt.line
                ? { ...symbol, generate_status: 'running', generate_partial: '' }
                : symbol
            ),
          }))
          setStatusMsg(`生成注释: ${evt.name}`)
          break
        case 'comment_chunk':
          setSymbolsByPath(prev => ({
            ...prev,
            [selectedFile]: (prev[selectedFile] ?? []).map(symbol =>
              symbol.line_start === evt.line
                ? { ...symbol, generate_status: 'running', generate_partial: evt.partial }
                : symbol
            ),
          }))
          break
        case 'comment_done':
          setSymbolsByPath(prev => {
            const nextSymbols = (prev[selectedFile] ?? []).map(symbol =>
              symbol.line_start === evt.line
                ? {
                    ...symbol,
                    generate_status: 'done',
                    generated_comment: evt.comment,
                    generate_partial: undefined,
                  }
                : symbol
            )
            queueMicrotask(() => syncFileFromSymbols(selectedFile, nextSymbols))
            return { ...prev, [selectedFile]: nextSymbols }
          })
          break
        case 'all_done':
          setGeneratingPath(null)
          updateFileEntry(selectedFile, entry => ({ ...entry, status: 'done' }))
          setStatusMsg(`✓ 已生成 ${evt.count} 条注释，先预览 diff 再写入文件`)
          wsRef.current = null
          break
        case 'error':
          setGeneratingPath(null)
          updateFileEntry(selectedFile, entry => ({ ...entry, status: 'error' }))
          setStatusMsg(`✗ ${evt.message}`)
          wsRef.current = null
          break
      }
    }

    wsRef.current = startHeaderGeneration(
      settings,
      selectedFile,
      targetSymbols.map(symbol => symbol.line_start),
      config,
      onEvent,
    )
  }, [config, generatingPath, selectedFile, settings, symbolsByPath, syncFileFromSymbols, updateFileEntry])

  const handleStop = useCallback(() => {
    wsRef.current?.stop()
    wsRef.current = null
    setGeneratingPath(null)
    if (selectedFile) {
      updateFileEntry(selectedFile, entry => ({ ...entry, status: 'pending' }))
    }
    setStatusMsg('已停止')
  }, [selectedFile, updateFileEntry])

  const handlePreview = useCallback(async () => {
    if (!selectedFile) return
    const comments = generatedCommentsFor(selectedFile)
    if (Object.keys(comments).length === 0) {
      setStatusMsg('当前文件还没有生成注释')
      return
    }

    setPreviewingPath(selectedFile)
    setStatusMsg('生成 diff 预览…')
    const result = await apiPreviewComments(selectedFile, comments, config.replaceExisting)
    setPreviewingPath(null)

    if (!result.ok) {
      setStatusMsg(`✗ ${result.error ?? '预览失败'}`)
      return
    }

    setDiffsByPath(prev => ({ ...prev, [selectedFile]: result.diff }))
    setStatusMsg('✓ 已生成 diff 预览')
  }, [config.replaceExisting, selectedFile, symbolsByPath])

  const handleApply = useCallback(async () => {
    if (!selectedFile) return
    const comments = generatedCommentsFor(selectedFile)
    if (Object.keys(comments).length === 0) {
      setStatusMsg('当前文件没有可写入的注释')
      return
    }

    setStatusMsg('写入文件…')
    const result = await apiApplyComments(selectedFile, comments, config.replaceExisting)
    if (!result.ok) {
      setStatusMsg(`✗ ${result.error ?? '写入失败'}`)
      updateFileEntry(selectedFile, entry => ({ ...entry, status: 'error' }))
      return
    }

    setDiffsByPath(prev => {
      const next = { ...prev }
      delete next[selectedFile]
      return next
    })
    await loadFile(selectedFile, true)
    setStatusMsg(`✓ 已写入 ${Object.keys(comments).length} 条注释`)
  }, [config.replaceExisting, loadFile, selectedFile, updateFileEntry, symbolsByPath])

  return (
    <div className="page-layout">
      <div className="header-workspace">
        <div
          className="sidebar"
          onDragEnter={e => { e.preventDefault(); dragCounter.current++; setDragging(true) }}
          onDragLeave={() => { dragCounter.current--; if (dragCounter.current === 0) setDragging(false) }}
          onDragOver={e => e.preventDefault()}
          onDrop={handleDrop}
        >
          <div className="sidebar-title">HEADERS</div>

          {files.length === 0 ? (
            <div className={`drop-zone ${dragging ? 'drag-over' : ''}`} onClick={handleBrowseDir}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
              </svg>
              <span>拖放头文件或文件夹</span>
              <span style={{ fontSize: 11, opacity: .6 }}>支持批量导入并按文件切换</span>
            </div>
          ) : (
            <div className={`file-list ${dragging ? 'drag-over' : ''}`}>
              {files.map(file => (
                <div
                  key={file.path}
                  className={`file-item ${selectedFile === file.path ? 'active' : ''}`}
                  onClick={() => void handleSelectFile(file.path)}
                  title={file.path}
                >
                  <span className={`fi-badge ${file.status}`}>{FILE_ICONS[file.status]}</span>
                  <span className="fi-name">{file.name}</span>
                  <span className="fi-note">
                    {file.generatedCount}/{file.symbolCount || '—'}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="sidebar-btns">
            <button className="btn btn-ghost" style={{ flex: 1, height: 28, fontSize: 12 }} onClick={handleBrowseDir}>
              📁 文件夹
            </button>
            <button className="btn btn-ghost" style={{ flex: 1, height: 28, fontSize: 12 }} onClick={handleBrowseFile}>
              📄 文件
            </button>
            <button
              className="btn btn-ghost"
              style={{ height: 28, fontSize: 12, padding: '0 10px' }}
              onClick={() => {
                wsRef.current?.stop()
                wsRef.current = null
                setFiles([])
                setSymbolsByPath({})
                setDiffsByPath({})
                setSelectedFile(null)
                setGeneratingPath(null)
                setPreviewingPath(null)
                setStatusMsg('已清空头文件列表')
              }}
            >
              清空
            </button>
          </div>
        </div>

        <div className="header-main">
          <div className="header-main-toolbar">
            <div className="header-toolbar-group">
              <label className="hdr-check">
                <input
                  type="checkbox"
                  checked={config.replaceExisting}
                  onChange={e => updateConfig({ replaceExisting: e.target.checked })}
                />
                <span>替换已有注释</span>
              </label>
              <label className="hdr-check">
                <input
                  type="checkbox"
                  checked={config.includeBrief}
                  onChange={e => updateConfig({ includeBrief: e.target.checked })}
                />
                <span>@brief</span>
              </label>
              <label className="hdr-check">
                <input
                  type="checkbox"
                  checked={config.includeParams}
                  onChange={e => updateConfig({ includeParams: e.target.checked })}
                />
                <span>@param</span>
              </label>
              <label className="hdr-check">
                <input
                  type="checkbox"
                  checked={config.includeReturn}
                  onChange={e => updateConfig({ includeReturn: e.target.checked })}
                />
                <span>@return</span>
              </label>
            </div>

            <div className="header-toolbar-group header-toolbar-inputs">
              <input
                className="header-config-input"
                value={config.author}
                onChange={e => updateConfig({ author: e.target.value })}
                placeholder="作者标签（可选）"
              />
              <label className="hdr-check">
                <input
                  type="checkbox"
                  checked={config.includeDate}
                  onChange={e => updateConfig({ includeDate: e.target.checked })}
                />
                <span>附带日期</span>
              </label>
              <input
                className="header-config-input header-config-input-small"
                value={config.dateFormat}
                onChange={e => updateConfig({ dateFormat: e.target.value })}
                placeholder="%Y-%m-%d"
              />
              <input
                className="header-config-input"
                value={config.customTags.map(tag => tag.value ? `${tag.name}:${tag.value}` : tag.name).join(', ')}
                onChange={e => {
                  const tags = e.target.value
                    .split(',')
                    .map(part => part.trim())
                    .filter(Boolean)
                    .map(part => {
                      const idx = part.indexOf(':')
                      return idx >= 0
                        ? { name: part.slice(0, idx).trim(), value: part.slice(idx + 1).trim() }
                        : { name: part, value: '' }
                    })
                  updateConfig({ customTags: tags })
                }}
                placeholder="自定义标签，例：since:v2, owner:core"
              />
            </div>

            <div className="header-toolbar-group">
              <button className="btn btn-ghost" onClick={selectAll} disabled={!selectedFile || currentSymbols.length === 0}>
                全选
              </button>
              <button className="btn btn-ghost" onClick={selectNone} disabled={!selectedFile || currentSymbols.length === 0}>
                全取消
              </button>
              <button className="btn btn-ghost" onClick={selectNoComment} disabled={!selectedFile || currentSymbols.length === 0}>
                仅无注释
              </button>
              <button
                className="btn btn-primary"
                onClick={generatingPath ? handleStop : handleGenerate}
                disabled={!selectedFile || currentSymbols.length === 0 || isScanning}
              >
                {generatingPath ? '■ 停止' : '▶ 生成注释'}
              </button>
              <button
                className="btn btn-blue"
                onClick={handlePreview}
                disabled={!selectedFile || previewingPath === selectedFile || generatedCount === 0}
              >
                {previewingPath === selectedFile ? '预览中…' : 'Diff 预览'}
              </button>
              <button
                className="btn btn-apply"
                onClick={handleApply}
                disabled={!selectedFile || generatedCount === 0 || !previewReady}
              >
                写入文件
              </button>
            </div>
          </div>

          <div className="header-main-body">
            <div className="header-symbol-list">
              <div className="content-header">
                {selectedFile ?? '— 左侧导入头文件并选择一个文件开始 —'}
              </div>

              {selectedFile && currentSymbols.length === 0 && (
                <div className="header-empty">
                  {isScanning ? '扫描中…' : '未找到变量或函数声明'}
                </div>
              )}

              {!selectedFile && (
                <div className="header-empty">
                  选择文件后可查看函数、变量、实现定位和生成状态
                </div>
              )}

              {currentSymbols.map(symbol => (
                <SymbolCard
                  key={`${symbol.kind}-${symbol.line_start}`}
                  symbol={symbol}
                  expanded={expandedLine === symbol.line_start}
                  onToggleExpand={() => setExpandedLine(prev => prev === symbol.line_start ? null : symbol.line_start)}
                  onToggleSelect={() => toggleSelect(symbol.line_start)}
                />
              ))}
            </div>

            <div className="header-preview-pane">
              <div className="content-header">
                Diff 预览
              </div>
              <div className="header-diff-wrap">
                {currentDiff ? (
                  <pre className="header-diff-code">{currentDiff}</pre>
                ) : (
                  <div className="header-empty">
                    生成注释后点击 “Diff 预览”，确认差异再写入文件
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="bottom-bar">
        {generatingPath && (
          <div className="progress-track" style={{ width: 220, marginRight: 10 }}>
            <div
              className="progress-fill"
              style={{ width: `${selectedSymbols.length ? (generatedCount / selectedSymbols.length) * 100 : 0}%` }}
            />
          </div>
        )}
        <div className="status-msg">{statusMsg}</div>
      </div>
    </div>
  )
}

interface SymbolCardProps {
  symbol: HeaderSymbol
  expanded: boolean
  onToggleExpand: () => void
  onToggleSelect: () => void
}

function SymbolCard({ symbol, expanded, onToggleExpand, onToggleSelect }: SymbolCardProps) {
  const status = symbol.generate_status ?? 'idle'
  const partial = symbol.generate_partial ?? ''
  const generated = symbol.generated_comment ?? ''

  const statusIcon =
    status === 'running' ? '◐'
    : status === 'done' ? '●'
    : symbol.has_comment ? '○'
    : '—'

  const statusClass =
    status === 'running' ? 'running'
    : status === 'done' ? 'done'
    : symbol.has_comment ? 'has-comment'
    : 'no-comment'

  return (
    <div className={`func-card ${expanded ? 'expanded' : ''}`}>
      <div className="func-card-row" onDoubleClick={onToggleExpand}>
        <input
          type="checkbox"
          className="func-select"
          checked={!!symbol.selected}
          onChange={onToggleSelect}
          onClick={e => e.stopPropagation()}
        />
        <span className={`func-status-icon ${statusClass}`}>{statusIcon}</span>
        <span className={`header-kind-badge ${symbol.kind}`}>{symbol.kind === 'function' ? '函数' : '变量'}</span>
        <span className="func-class">
          {symbol.namespace_context ? `${symbol.namespace_context}::` : ''}
          {symbol.class_context ? `${symbol.class_context}::` : ''}
        </span>
        <span className="func-name">{symbol.name}</span>
        <span className="func-line">L{symbol.line_start}</span>
        <span className={`func-badge ${symbol.has_comment ? 'has' : 'no'}`}>
          {symbol.has_comment ? '有注释' : '无注释'}
        </span>
        <button
          className="func-expand-btn"
          onClick={e => { e.stopPropagation(); onToggleExpand() }}
          title={expanded ? '收起' : '展开'}
        >
          {expanded ? '▲' : '▼'}
        </button>
      </div>

      {expanded && (
        <div className="func-card-body">
          <div className="func-section-label">声明</div>
          <pre className="func-code">{symbol.full_signature}</pre>

          {symbol.implementation_snippet && (
            <>
              <div className="func-section-label">
                实现定位 {symbol.implementation_path ? `· ${basename(symbol.implementation_path)}` : ''}
              </div>
              <pre className="func-code implementation">{symbol.implementation_snippet}</pre>
            </>
          )}

          {symbol.has_comment && symbol.existing_comment && (
            <>
              <div className="func-section-label">已有注释</div>
              <pre className="func-code existing">{symbol.existing_comment}</pre>
            </>
          )}

          {(status === 'running' || status === 'done') && (
            <>
              <div className="func-section-label">
                {status === 'running' ? '生成中…' : '生成的注释'}
              </div>
              <pre className="func-code generated">
                {status === 'running' ? (partial || '…') : generated}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}
