import { useCallback, useRef, useState } from 'react'
import type { FileEntry, FileFilterOption } from '../types'

const ICONS: Record<string, string> = {
  pending: '○', running: '◐', done: '●', error: '✗', skipped: '—',
}

interface Props {
  files: FileEntry[]
  selectedFile: string | null
  filters: FileFilterOption[]
  activeFilters: string[]
  onToggleFilter: (key: string) => void
  onSelect: (path: string) => void
  onToggleSelect: (path: string) => void
  onSelectAll: () => void
  onSelectNone: () => void
  onAddPaths: (paths: string[]) => void
  onClear: () => void
}

export default function FilePanel({
  files,
  selectedFile,
  filters,
  activeFilters,
  onToggleFilter,
  onSelect,
  onToggleSelect,
  onSelectAll,
  onSelectNone,
  onAddPaths,
  onClear,
}: Props) {
  const dragCounter = useRef(0)
  const [dragging, setDragging] = useState(false)

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current = 0
    setDragging(false)
    const paths = Array.from(e.dataTransfer.files).map(f => (f as any).path ?? '')
      .filter(Boolean)
    if (paths.length) onAddPaths(paths)
  }, [onAddPaths, setDragging])

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current++
    setDragging(true)
  }
  const handleDragLeave = () => {
    dragCounter.current--
    if (dragCounter.current === 0) setDragging(false)
  }

  // Native file picker via Electron IPC or fallback input
  const handleBrowseDir = async () => {
    const w = window as any
    if (w.electronAPI?.openDirectory) {
      const paths: string[] = await w.electronAPI.openDirectory()
      if (paths.length) onAddPaths(paths)
    }
  }
  const handleBrowseFile = async () => {
    const w = window as any
    if (w.electronAPI?.openFile) {
      const paths: string[] = await w.electronAPI.openFile()
      if (paths.length) onAddPaths(paths)
    }
  }

  return (
    <div
      className="sidebar"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={e => e.preventDefault()}
      onDrop={handleDrop}
    >
      <div className="sidebar-title">FILES</div>

      <div className="filter-panel">
        {filters.map(filter => (
          <label key={filter.key} className="filter-check">
            <input
              type="checkbox"
              checked={activeFilters.includes(filter.key)}
              onChange={() => onToggleFilter(filter.key)}
            />
            <span>{filter.label}</span>
          </label>
        ))}
      </div>

      {files.length === 0 ? (
        <div
          className={`drop-zone ${dragging ? 'drag-over' : ''}`}
          onClick={handleBrowseDir}
        >
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
          </svg>
          <span>拖放文件或文件夹</span>
          <span style={{ fontSize: 11, opacity: .6 }}>或点击此处选择</span>
        </div>
      ) : (
        <div className={`file-list ${dragging ? 'drag-over' : ''}`}>
          {files.map(f => (
            <div
              key={f.path}
              className={`file-item ${selectedFile === f.path ? 'active' : ''}`}
              onClick={() => onSelect(f.path)}
              title={f.path}
            >
              <input
                type="checkbox"
                className="fi-check"
                checked={f.selected}
                onChange={() => onToggleSelect(f.path)}
                onClick={e => e.stopPropagation()}
                title="选择此文件用于写回"
              />
              <span className={`fi-badge ${f.status}`}>{ICONS[f.status]}</span>
              <span className="fi-name">{f.name}</span>
              {f.total > 0 && (
                <span className="fi-note">
                  {f.translated}/{f.total}
                  {(f.untranslated ?? 0) > 0 ? ` !${f.untranslated}` : ''}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="sidebar-btns">
        <button className="btn btn-ghost" style={{ height: 28, fontSize: 12, padding: '0 10px' }} onClick={onSelectAll}>
          全选
        </button>
        <button className="btn btn-ghost" style={{ height: 28, fontSize: 12, padding: '0 10px' }} onClick={onSelectNone}>
          全不选
        </button>
        <button className="btn btn-ghost" style={{ flex: 1, height: 28, fontSize: 12 }} onClick={handleBrowseDir}>
          📁 文件夹
        </button>
        <button className="btn btn-ghost" style={{ flex: 1, height: 28, fontSize: 12 }} onClick={handleBrowseFile}>
          📄 文件
        </button>
        <button className="btn btn-ghost" style={{ height: 28, fontSize: 12, padding: '0 10px' }} onClick={onClear}>
          清空
        </button>
      </div>
    </div>
  )
}
