import type { Settings } from '../types'

interface Props {
  settings: Settings
  models: string[]
  modelsLoading: boolean
  modelsMsg?: string
  onSettings: (s: Settings) => void
  onCheck: () => void
  onReloadModels: () => void
  checkMsg?: string
}

export default function Toolbar({
  settings,
  models,
  modelsLoading,
  modelsMsg,
  onSettings,
  onCheck,
  onReloadModels,
  checkMsg,
}: Props) {
  const set = (partial: Partial<Settings>) => onSettings({ ...settings, ...partial })
  const selectedModel = models.includes(settings.model) ? settings.model : '__custom__'

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
      <select
        className="model-select"
        value={selectedModel}
        onChange={e => {
          const value = e.target.value
          if (value !== '__custom__') {
            set({ model: value })
          }
        }}
        disabled={modelsLoading || models.length === 0}
      >
        {models.length === 0 ? (
          <option value="__custom__">无可用模型</option>
        ) : (
          <>
            {models.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
            <option value="__custom__">自定义模型…</option>
          </>
        )}
      </select>
      <input
        className="model"
        value={settings.model}
        onChange={e => set({ model: e.target.value })}
        placeholder={modelsLoading ? '加载模型中…' : '可手输任意模型名'}
      />
      <button
        className="btn btn-ghost toolbar-mini-btn"
        onClick={onReloadModels}
        disabled={modelsLoading}
        title="从当前 Ollama Host 读取模型列表"
      >
        {modelsLoading ? '读取中…' : '刷新模型'}
      </button>
      {modelsMsg && (
        <span className="toolbar-model-msg">{modelsMsg}</span>
      )}

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
