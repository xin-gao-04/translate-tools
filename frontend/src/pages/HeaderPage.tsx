import { useCallback, useRef, useState } from 'react'
import type { FunctionInfo, HeaderWsEvent, Settings } from '../types'
import { apiAnalyzeHeader, apiApplyComments, startHeaderGeneration } from '../api'

interface Props { settings: Settings }

export default function HeaderPage({ settings }: Props) {
  const [filePath,       setFilePath]       = useState<string | null>(null)
  const [functions,      setFunctions]      = useState<FunctionInfo[]>([])
  const [isAnalyzing,    setIsAnalyzing]    = useState(false)
  const [isGenerating,   setIsGenerating]   = useState(false)
  const [replaceExisting, setReplaceExisting] = useState(false)
  const [statusMsg,      setStatusMsg]      = useState('拖放或选择 .h / .hpp 头文件')
  const [expandedLine,   setExpandedLine]   = useState<number | null>(null)
  const wsRef = useRef<{ stop: () => void } | null>(null)
  const dragCounter = useRef(0)
  const [dragging,       setDragging]       = useState(false)

  // ── File drop / browse ────────────────────────────────────────────────────

  const loadFile = useCallback(async (path: string) => {
    setFilePath(path)
    setFunctions([])
    setIsAnalyzing(true)
    setStatusMsg('分析中…')
    const result = await apiAnalyzeHeader(path)
    if (result.error) {
      setStatusMsg(`✗ ${result.error}`)
    } else {
      const funcs = (result.functions ?? []).map((f: FunctionInfo) => ({
        ...f,
        selected: !f.has_comment,
        generate_status: 'idle' as const,
      }))
      setFunctions(funcs)
      const noComment = funcs.filter(f => !f.has_comment).length
      setStatusMsg(`已找到 ${funcs.length} 个函数，其中 ${noComment} 个无注释`)
    }
    setIsAnalyzing(false)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current = 0
    setDragging(false)
    const file = Array.from(e.dataTransfer.files).find(f =>
      /\.(h|hpp|hxx|hh)$/i.test(f.name)
    )
    if (file) loadFile((file as any).path ?? '')
  }, [loadFile])

  const handleBrowse = async () => {
    const w = window as any
    if (w.electronAPI?.openFile) {
      const paths: string[] = await w.electronAPI.openFile()
      if (paths.length) loadFile(paths[0])
    }
  }

  // ── Selection helpers ─────────────────────────────────────────────────────

  const toggleSelect = (lineStart: number) => {
    setFunctions(fs => fs.map(f =>
      f.line_start === lineStart ? { ...f, selected: !f.selected } : f
    ))
  }

  const selectAll    = () => setFunctions(fs => fs.map(f => ({ ...f, selected: true })))
  const selectNone   = () => setFunctions(fs => fs.map(f => ({ ...f, selected: false })))
  const selectNoComment = () => setFunctions(fs => fs.map(f => ({ ...f, selected: !f.has_comment })))

  // ── Generation ────────────────────────────────────────────────────────────

  const handleGenerate = useCallback(() => {
    if (!filePath || isGenerating) return
    const selected = functions.filter(f =>
      f.selected && (replaceExisting || !f.has_comment)
    )
    if (selected.length === 0) {
      setStatusMsg('没有需要生成的函数（请检查选中项）')
      return
    }

    setIsGenerating(true)
    setStatusMsg('生成注释中…')

    // Reset statuses for selected functions
    setFunctions(fs => fs.map(f =>
      selected.some(s => s.line_start === f.line_start)
        ? { ...f, generate_status: 'idle', generated_comment: undefined, generate_partial: undefined }
        : f
    ))

    const onEvent = (evt: HeaderWsEvent) => {
      switch (evt.type) {
        case 'function_started':
          setFunctions(fs => fs.map(f =>
            f.line_start === evt.line
              ? { ...f, generate_status: 'running', generate_partial: '' }
              : f
          ))
          setStatusMsg(`生成注释: ${evt.name}`)
          break
        case 'comment_chunk':
          setFunctions(fs => fs.map(f =>
            f.name === evt.name && f.generate_status === 'running'
              ? { ...f, generate_partial: evt.partial }
              : f
          ))
          break
        case 'comment_done':
          setFunctions(fs => fs.map(f =>
            f.line_start === evt.line
              ? { ...f, generate_status: 'done', generated_comment: evt.comment, generate_partial: undefined }
              : f
          ))
          break
        case 'all_done':
          setIsGenerating(false)
          setStatusMsg(`✓ 已为 ${evt.count} 个函数生成注释，点击「写入文件」应用`)
          wsRef.current = null
          break
        case 'error':
          setIsGenerating(false)
          setStatusMsg(`✗ ${evt.message}`)
          wsRef.current = null
          break
      }
    }

    wsRef.current = startHeaderGeneration(
      settings,
      filePath,
      selected.map(f => f.line_start),
      replaceExisting,
      onEvent,
    )
  }, [filePath, functions, isGenerating, replaceExisting, settings])

  const handleStop = useCallback(() => {
    wsRef.current?.stop()
    wsRef.current = null
    setIsGenerating(false)
    setStatusMsg('已停止')
  }, [])

  const handleApply = useCallback(async () => {
    if (!filePath) return
    const comments: Record<string, string> = {}
    functions.forEach(f => {
      if (f.generated_comment) comments[String(f.line_start)] = f.generated_comment
    })
    if (Object.keys(comments).length === 0) return

    setStatusMsg('写入文件…')
    const result = await apiApplyComments(filePath, comments, replaceExisting)
    if (result.ok) {
      setStatusMsg(`✓ 已写入 ${Object.keys(comments).length} 条注释`)
      // Mark applied functions as having comments now
      setFunctions(fs => fs.map(f =>
        f.generated_comment
          ? { ...f, has_comment: true, existing_comment: f.generated_comment!, generated_comment: undefined, generate_status: 'idle' }
          : f
      ))
    } else {
      setStatusMsg(`✗ ${result.error}`)
    }
  }, [filePath, functions, replaceExisting])

  // ── Render ────────────────────────────────────────────────────────────────

  const hasDone = functions.some(f => f.generate_status === 'done')

  return (
    <div className="page-layout">
      <div className="header-page">

        {/* ── Left panel ── */}
        <div className="header-sidebar">
          <div className="sidebar-title">HEAD FILE</div>

          {/* Drop zone */}
          <div
            className={`header-drop ${dragging ? 'drag-over' : ''}`}
            onClick={handleBrowse}
            onDragEnter={e => { e.preventDefault(); dragCounter.current++; setDragging(true) }}
            onDragLeave={() => { dragCounter.current--; if (dragCounter.current === 0) setDragging(false) }}
            onDragOver={e => e.preventDefault()}
            onDrop={handleDrop}
          >
            {filePath
              ? <span className="header-file-name">{filePath.split('/').pop()}</span>
              : <>
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8L14 2z"/>
                    <polyline points="14 2 14 8 20 8"/>
                  </svg>
                  <span>.h / .hpp</span>
                  <span style={{ fontSize: 11, opacity: .6 }}>拖放或点击选择</span>
                </>
            }
          </div>

          {/* Options */}
          <div className="header-options">
            <label className="hdr-check">
              <input
                type="checkbox"
                checked={replaceExisting}
                onChange={e => setReplaceExisting(e.target.checked)}
              />
              <span>替换已有注释</span>
            </label>
          </div>

          {/* Selection shortcuts */}
          {functions.length > 0 && (
            <div className="header-sel-row">
              <button className="btn btn-ghost" style={{ flex: 1, height: 26, fontSize: 11 }} onClick={selectAll}>全选</button>
              <button className="btn btn-ghost" style={{ flex: 1, height: 26, fontSize: 11 }} onClick={selectNone}>全取消</button>
              <button className="btn btn-ghost" style={{ flex: 1, height: 26, fontSize: 11 }} onClick={selectNoComment}>仅无注释</button>
            </div>
          )}

          <div style={{ flex: 1 }} />

          {/* Action buttons */}
          <div className="header-actions">
            <button
              className="btn btn-primary"
              style={{ width: '100%', marginBottom: 6 }}
              onClick={isGenerating ? handleStop : handleGenerate}
              disabled={!filePath || functions.length === 0 || isAnalyzing}
            >
              {isGenerating ? '■ 停止' : '▶ 生成注释'}
            </button>
            <button
              className="btn btn-apply"
              style={{ width: '100%' }}
              onClick={handleApply}
              disabled={!hasDone}
            >
              写入文件
            </button>
          </div>
        </div>

        {/* ── Function list ── */}
        <div className="header-content">
          {functions.length === 0 && !isAnalyzing && (
            <div className="header-empty">
              {filePath ? '未找到函数声明' : '选择头文件开始分析'}
            </div>
          )}
          {isAnalyzing && (
            <div className="header-empty">分析中…</div>
          )}
          {functions.map(f => (
            <FuncCard
              key={f.line_start}
              func={f}
              expanded={expandedLine === f.line_start}
              onToggleExpand={() => setExpandedLine(expandedLine === f.line_start ? null : f.line_start)}
              onToggleSelect={() => toggleSelect(f.line_start)}
            />
          ))}
        </div>
      </div>

      <div className="bottom-bar">
        {isGenerating && (
          <div className="progress-track" style={{ width: 200, marginRight: 10 }}>
            <div className="progress-fill"
              style={{ width: `${functions.filter(f => f.generate_status === 'done').length / Math.max(1, functions.filter(f => f.selected).length) * 100}%` }}
            />
          </div>
        )}
        <div className="status-msg">{statusMsg}</div>
      </div>
    </div>
  )
}

// ── FuncCard ──────────────────────────────────────────────────────────────────

interface FuncCardProps {
  func: FunctionInfo
  expanded: boolean
  onToggleExpand: () => void
  onToggleSelect: () => void
}

function FuncCard({ func, expanded, onToggleExpand, onToggleSelect }: FuncCardProps) {
  const status = func.generate_status ?? 'idle'
  const partial = func.generate_partial ?? ''
  const generated = func.generated_comment ?? ''

  const statusIcon =
    status === 'running' ? '◐'
    : status === 'done'  ? '●'
    : func.has_comment   ? '○'
    : '—'

  const statusClass =
    status === 'running' ? 'running'
    : status === 'done'  ? 'done'
    : func.has_comment   ? 'has-comment'
    : 'no-comment'

  return (
    <div className={`func-card ${expanded ? 'expanded' : ''}`}>
      {/* Header row */}
      <div className="func-card-row" onDoubleClick={onToggleExpand}>
        <input
          type="checkbox"
          className="func-select"
          checked={!!func.selected}
          onChange={onToggleSelect}
          onClick={e => e.stopPropagation()}
        />
        <span className={`func-status-icon ${statusClass}`}>{statusIcon}</span>
        <span className="func-class">{func.class_context ? `${func.class_context}::` : ''}</span>
        <span className="func-name">{func.name}</span>
        <span className="func-line">L{func.line_start}</span>
        <span className={`func-badge ${func.has_comment ? 'has' : 'no'}`}>
          {func.has_comment ? '有注释' : '无注释'}
        </span>
        <button
          className="func-expand-btn"
          onClick={e => { e.stopPropagation(); onToggleExpand() }}
          title={expanded ? '收起' : '展开'}
        >
          {expanded ? '▲' : '▼'}
        </button>
      </div>

      {/* Expanded panel */}
      {expanded && (
        <div className="func-card-body">
          {/* Signature */}
          <div className="func-section-label">函数签名</div>
          <pre className="func-code">{func.full_signature}</pre>

          {/* Existing comment */}
          {func.has_comment && func.existing_comment && (
            <>
              <div className="func-section-label">已有注释</div>
              <pre className="func-code existing">{func.existing_comment}</pre>
            </>
          )}

          {/* Generated comment (streaming or final) */}
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
