import { useEffect, useRef, useState } from 'react'
import type { CommentRow } from '../types'

const STYLE_LABEL: Record<string, string> = {
  line: '//', block: '/* */', doc: '/**',
}

interface Props {
  rows: CommentRow[] | null
}

export default function CommentTable({ rows }: Props) {
  const tbodyRef = useRef<HTMLTableSectionElement>(null)
  const [expandedLine, setExpandedLine] = useState<number | null>(null)

  useEffect(() => {
    const running = tbodyRef.current?.querySelector('tr.row-running')
    running?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })

  if (!rows) {
    return (
      <div className="comment-table-wrap" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#94A3B8', fontSize: 13,
      }}>
        在左侧选择文件查看注释
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="comment-table-wrap" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#94A3B8', fontSize: 13,
      }}>
        未找到注释
      </div>
    )
  }

  return (
    <div className="comment-table-wrap">
      <table className="comment-table">
        <thead>
          <tr>
            <th>行号</th>
            <th>类型</th>
            <th>原文</th>
            <th>译文</th>
            <th></th>
          </tr>
        </thead>
        <tbody ref={tbodyRef}>
          {rows.map(row => (
            <Row
              key={row.lineno}
              row={row}
              expanded={expandedLine === row.lineno}
              onToggle={() => setExpandedLine(prev => prev === row.lineno ? null : row.lineno)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Row({ row, expanded, onToggle }: { row: CommentRow; expanded: boolean; onToggle: () => void }) {
  const disp = (t: string) => t.replace(/\n/g, ' ↵ ').slice(0, 200)

  const trClass = row.isEnglish
    ? row.status === 'running' ? 'td-tr running'
    : row.status === 'done'   ? 'td-tr'
    : 'td-tr empty'
    : 'td-tr skipped'

  const trText = !row.isEnglish ? '—'
    : row.status === 'running' ? (row.translated ? disp(row.translated) + ' …' : '翻译中…')
    : row.translated ? disp(row.translated)
    : ''

  return (
    <>
      <tr
        className={`${row.status === 'running' ? 'row-running' : ''} ${expanded ? 'row-expanded-header' : ''}`}
        onDoubleClick={onToggle}
        style={{ cursor: 'pointer' }}
        title="双击展开/收起上下文"
      >
        <td className="td-lineno">L{row.lineno}</td>
        <td className="td-style">{STYLE_LABEL[row.style] ?? row.style}</td>
        <td className="td-orig" title={row.original}>{disp(row.original)}</td>
        <td className={trClass} title={row.translated}>{trText}</td>
        <td className={`td-status ${row.isEnglish ? row.status : 'skipped'}`}>
          {row.isEnglish
            ? { pending: '○', running: '◐', done: '●', skipped: '—', error: '✗' }[row.status]
            : '—'}
        </td>
      </tr>

      {expanded && (
        <tr className="row-expanded-body">
          <td colSpan={5} style={{ padding: 0 }}>
            <ContextPanel row={row} />
          </td>
        </tr>
      )}
    </>
  )
}

function ContextPanel({ row }: { row: CommentRow }) {
  const commentLines = row.original.split('\n')
  const lineEnd = row.lineEnd ?? (row.lineno + commentLines.length - 1)

  return (
    <div className="context-panel">
      <div className="context-panel-cols">
        <div className="context-source">
          <div className="context-label">源码上下文</div>
          <div className="context-code">
            {(row.contextBefore ?? []).map((line, i) => {
              const lineNo = row.lineno - (row.contextBefore?.length ?? 0) + i
              return (
                <div key={i} className="ctx-line before">
                  <span className="ctx-lineno">{lineNo}</span>
                  <span className="ctx-text">{line || '\u00a0'}</span>
                </div>
              )
            })}
            {commentLines.map((line, i) => (
              <div key={`c${i}`} className="ctx-line comment-line">
                <span className="ctx-lineno">{row.lineno + i}</span>
                <span className="ctx-text">{line}</span>
              </div>
            ))}
            {(row.contextAfter ?? []).map((line, i) => (
              <div key={`a${i}`} className="ctx-line after">
                <span className="ctx-lineno">{lineEnd + 1 + i}</span>
                <span className="ctx-text">{line || '\u00a0'}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="context-translation">
          <div className="context-label">
            {row.status === 'done' ? '译文' : row.status === 'running' ? '翻译中…' : '原文（待翻译）'}
          </div>
          <div className="context-code">
            <pre className="ctx-translated">
              {row.status === 'done' || row.status === 'running'
                ? (row.translated || '…')
                : row.original}
            </pre>
          </div>
        </div>
      </div>
    </div>
  )
}
