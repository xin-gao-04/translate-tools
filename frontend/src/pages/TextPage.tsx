import { useCallback, useRef, useState } from 'react'
import type { Settings, TextTranslateDirection, TextWsEvent } from '../types'
import { startTextTranslation } from '../api'

interface Props { settings: Settings }

const TEXT_DIRECTION_META: Record<TextTranslateDirection, {
  readyStatus: string
  inputLabel: string
  inputPlaceholder: string
  outputLabel: string
  outputPlaceholder: string
}> = {
  en_to_zh: {
    readyStatus: '粘贴英文内容，点击「翻译」开始',
    inputLabel: '原文（英文）',
    inputPlaceholder: '在此粘贴英文内容…',
    outputLabel: '译文（中文）',
    outputPlaceholder: '翻译结果将显示在此处…',
  },
  zh_to_en: {
    readyStatus: '粘贴中文内容，点击「翻译」开始',
    inputLabel: '原文（中文）',
    inputPlaceholder: '在此粘贴中文内容…',
    outputLabel: '译文（英文）',
    outputPlaceholder: 'Translation result will appear here…',
  },
}

export default function TextPage({ settings }: Props) {
  const [direction,  setDirection]  = useState<TextTranslateDirection>('en_to_zh')
  const [inputText,  setInputText]  = useState('')
  const [outputText, setOutputText] = useState('')
  const [isRunning,  setIsRunning]  = useState(false)
  const [progress,   setProgress]   = useState({ idx: 0, total: 1 })
  const [statusMsg,  setStatusMsg]  = useState('粘贴英文内容，点击「翻译」开始')
  const wsRef = useRef<{ stop: () => void } | null>(null)
  const directionMeta = TEXT_DIRECTION_META[direction]

  const handleTranslate = useCallback(() => {
    if (!inputText.trim() || isRunning) return
    setIsRunning(true)
    setOutputText('')
    setStatusMsg('翻译中…')
    setProgress({ idx: 0, total: 1 })

    const onEvent = (evt: TextWsEvent) => {
      switch (evt.type) {
        case 'chunk':
          setOutputText(evt.partial)
          setProgress({ idx: evt.idx + 1, total: evt.total })
          setStatusMsg(`翻译中… ${evt.idx + 1} / ${evt.total} 段`)
          break
        case 'done':
          setOutputText(evt.text)
          setIsRunning(false)
          setStatusMsg('✓ 翻译完成')
          wsRef.current = null
          break
        case 'error':
          setIsRunning(false)
          setStatusMsg(`✗ ${evt.message}`)
          wsRef.current = null
          break
      }
    }

    wsRef.current = startTextTranslation(settings, inputText, direction, onEvent)
  }, [direction, inputText, isRunning, settings])

  const handleStop = useCallback(() => {
    wsRef.current?.stop()
    wsRef.current = null
    setIsRunning(false)
    setStatusMsg('已停止')
  }, [])

  const handleCopy = useCallback(() => {
    if (!outputText) return
    navigator.clipboard.writeText(outputText)
    setStatusMsg('✓ 已复制到剪贴板')
  }, [outputText])

  const handleClear = useCallback(() => {
    setInputText('')
    setOutputText('')
    setStatusMsg(directionMeta.readyStatus)
  }, [directionMeta.readyStatus])

  const handleChangeDirection = useCallback((nextDirection: TextTranslateDirection) => {
    if (isRunning) return
    setDirection(nextDirection)
    setOutputText('')
    setStatusMsg(TEXT_DIRECTION_META[nextDirection].readyStatus)
  }, [isRunning])

  return (
    <div className="page-layout">
      <div className="text-page">
        <div className="text-toolbar">
          <span className="text-toolbar-label">翻译方向</span>
          <select
            className="text-direction-select"
            value={direction}
            onChange={e => handleChangeDirection(e.target.value as TextTranslateDirection)}
            disabled={isRunning}
          >
            <option value="en_to_zh">英文 → 中文</option>
            <option value="zh_to_en">中文 → 英文</option>
          </select>
        </div>

        <div className="text-workarea">
          <div className="text-pane">
            <div className="text-pane-header">
              <span>{directionMeta.inputLabel}</span>
              <span className="text-pane-count">{inputText.length} 字符</span>
            </div>
            <textarea
              className="text-area"
              placeholder={directionMeta.inputPlaceholder}
              value={inputText}
              onChange={e => setInputText(e.target.value)}
              disabled={isRunning}
              spellCheck={false}
            />
          </div>

          <div className="text-divider">
            <div className="text-divider-line" />
            <button
              className="btn btn-primary text-translate-btn"
              onClick={isRunning ? handleStop : handleTranslate}
              disabled={!isRunning && !inputText.trim()}
            >
              {isRunning ? '■ 停止' : '▶ 翻译'}
            </button>
            {isRunning && (
              <div className="text-progress-bar">
              <div
                className="text-progress-fill"
                style={{ height: `${(progress.idx / progress.total) * 100}%` }}
              />
              </div>
            )}
            <div className="text-divider-line" />
          </div>

          <div className="text-pane">
            <div className="text-pane-header">
              <span>{directionMeta.outputLabel}</span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  className="btn btn-ghost"
                  style={{ height: 24, fontSize: 11, padding: '0 10px' }}
                  onClick={handleCopy}
                  disabled={!outputText}
                >
                  复制
                </button>
                <button
                  className="btn btn-ghost"
                  style={{ height: 24, fontSize: 11, padding: '0 10px' }}
                  onClick={handleClear}
                >
                  清空
                </button>
              </div>
            </div>
            <textarea
              className="text-area output"
              placeholder={directionMeta.outputPlaceholder}
              value={outputText}
              readOnly
              spellCheck={false}
            />
          </div>
        </div>
      </div>

      <div className="bottom-bar">
        <div className="status-msg">{statusMsg}</div>
      </div>
    </div>
  )
}
