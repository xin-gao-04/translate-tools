import type { Settings } from '../types'

interface Props {
  settings: Settings
  onSettings: (s: Settings) => void
  onCheck: () => void
  checkMsg?: string
}

export default function Toolbar({ settings, onSettings, onCheck, checkMsg }: Props) {
  const set = (partial: Partial<Settings>) => onSettings({ ...settings, ...partial })

  return (
    <div className="toolbar">
      <span className="toolbar-title">translate-comments</span>
      <div className="toolbar-sep" />

      <label>Host</label>
      <input
        className="host"
        value={settings.host}
        onChange={e => set({ host: e.target.value })}
        placeholder="http://localhost:11434"
      />

      <div className="toolbar-sep" />
      <label>模型</label>
      <input
        className="model"
        list="model-list"
        value={settings.model}
        onChange={e => set({ model: e.target.value })}
        placeholder="qwen2.5:7b"
      />
      <datalist id="model-list">
        {['qwen2.5:7b','qwen2.5:14b','qwen2.5:32b','llama3.2','mistral'].map(m => (
          <option key={m} value={m} />
        ))}
      </datalist>

      <div className="toolbar-sep" />
      <label>输出</label>
      <select
        className="output"
        value={settings.outputMode}
        onChange={e => set({ outputMode: e.target.value as Settings['outputMode'] })}
      >
        <option value="stdout">stdout — 打印终端</option>
        <option value="inplace">inplace — 覆写原文件</option>
        <option value="diff">diff — 仅差异</option>
      </select>

      <div className="toolbar-sep" />
      <button className="btn btn-ghost" style={{ height: 28, fontSize: 12 }} onClick={onCheck}>
        🔗 检查连接
      </button>

      {checkMsg && (
        <span className="toolbar-check-msg">{checkMsg}</span>
      )}
    </div>
  )
}
