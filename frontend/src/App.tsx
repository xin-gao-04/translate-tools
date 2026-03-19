import { useEffect, useState } from 'react'
import type { Settings } from './types'
import { apiCheck, apiModels, setApiPort } from './api'
import Toolbar from './components/Toolbar'
import NavTabs, { type Page } from './components/NavTabs'
import FilePage   from './pages/FilePage'
import TextPage   from './pages/TextPage'
import HeaderPage from './pages/HeaderPage'

export default function App() {
  const [activePage, setActivePage] = useState<Page>('files')
  const [settings, setSettings] = useState<Settings>({
    host: 'http://localhost:11434',
    model: 'qwen2.5:7b',
    outputMode: 'stdout',
    apiPort: 8765,
  })
  const [checkMsg, setCheckMsg] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [modelsMsg, setModelsMsg] = useState('')

  // Resolve Electron API port on mount
  useEffect(() => {
    const w = window as any
    if (w.electronAPI?.getApiPort) {
      w.electronAPI.getApiPort().then((port: number) => {
        setApiPort(port)
        setSettings(s => ({ ...s, apiPort: port }))
      })
    }
  }, [])

  useEffect(() => {
    const host = settings.host.trim()
    if (!host) {
      setModels([])
      setModelsMsg('')
      return
    }

    const timer = window.setTimeout(() => {
      void loadModels(host)
    }, 400)

    return () => window.clearTimeout(timer)
  }, [settings.host])

  const loadModels = async (host = settings.host) => {
    setModelsLoading(true)
    const { ok, models, message } = await apiModels(host)
    setModelsLoading(false)
    setModels(models)
    setModelsMsg(ok ? `已读取 ${models.length} 个模型` : `模型列表获取失败: ${message}`)
  }

  const handleCheck = async () => {
    setCheckMsg('检查中…')
    const { ok, message } = await apiCheck(settings.host, settings.model)
    setCheckMsg(ok ? `✓ ${message}` : `✗ ${message}`)
    setTimeout(() => setCheckMsg(''), 5000)
  }

  return (
    <div className="app">
      <Toolbar
        settings={settings}
        models={models}
        modelsLoading={modelsLoading}
        modelsMsg={modelsMsg}
        onSettings={setSettings}
        onCheck={handleCheck}
        onReloadModels={() => void loadModels()}
        checkMsg={checkMsg}
      />
      <NavTabs active={activePage} onChange={setActivePage} />

      {activePage === 'files'  && <FilePage   settings={settings} />}
      {activePage === 'text'   && <TextPage   settings={settings} />}
      {activePage === 'header' && <HeaderPage settings={settings} />}
    </div>
  )
}
