interface Props {
  files: number
  doneFiles: number
  totalTranslated: number
  statusMsg: string
  isRunning: boolean
  canApply: boolean
  onStart: () => void
  onStop: () => void
  onApply: () => void
}

export default function BottomBar({
  files, doneFiles, totalTranslated,
  statusMsg, isRunning, canApply,
  onStart, onStop, onApply,
}: Props) {
  const pct = files > 0 ? (doneFiles / files) * 100 : 0

  return (
    <div className="bottom-bar">
      {/* Total progress */}
      <div className="progress-track" style={{ width: 160 }}>
        <div className="progress-fill total" style={{ width: `${pct}%` }} />
      </div>
      <div className="stats">
        {doneFiles} / {files} 文件 &nbsp;·&nbsp; {totalTranslated} 条已翻译
      </div>

      <div className="status-msg">{statusMsg}</div>

      {canApply && (
        <button className="btn btn-apply" onClick={onApply}>
          💾 应用到文件
        </button>
      )}

      {isRunning ? (
        <button className="btn btn-danger" onClick={onStop}>
          ■ 停止
        </button>
      ) : (
        <button className="btn btn-primary" onClick={onStart} disabled={files === 0}>
          ▶ 开始翻译
        </button>
      )}
    </div>
  )
}
