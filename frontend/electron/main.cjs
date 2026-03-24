/**
 * Electron main process.
 *
 * Development:  loads http://localhost:5173  (Vite dev server)
 * Production:   loads dist/index.html
 *
 * Starts the Python FastAPI backend as a child process and
 * kills it when the window closes.
 */

const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const fs = require('fs')
const path = require('path')
const { spawn } = require('child_process')
const http = require('http')

const isDev = process.argv.includes('--dev')
const API_PORT = 8765

let mainWindow = null
let backendProcess = null

// ── Start Python backend ──────────────────────────────────────────────────────

function resolvePythonLauncher () {
  const configured = process.env.TRANSLATE_COMMENTS_PYTHON?.trim()
  if (configured) {
    return { command: configured, prefixArgs: [] }
  }

  if (!isDev) {
    const bundledCandidates = [
      path.join(process.resourcesPath, 'backend-runtime', 'python.exe'),
      path.join(process.resourcesPath, 'backend-runtime', 'Scripts', 'python.exe'),
    ]

    for (const candidate of bundledCandidates) {
      if (fs.existsSync(candidate)) {
        return { command: candidate, prefixArgs: [] }
      }
    }
  }

  if (process.platform === 'win32') {
    return { command: 'py', prefixArgs: ['-3'] }
  }

  return { command: 'python3', prefixArgs: [] }
}

function resolveBackendLauncher () {
  if (!isDev) {
    const bundledExe = path.join(process.resourcesPath, 'backend', 'translate-comments-backend.exe')
    if (fs.existsSync(bundledExe)) {
      return {
        command: bundledExe,
        prefixArgs: [],
        cwd: path.dirname(bundledExe),
        extraPath: [],
      }
    }
  }

  const python = resolvePythonLauncher()
  const cwd = isDev
    ? path.join(__dirname, '..', '..')
    : path.join(process.resourcesPath, 'backend')
  const runtimeRoot = isDev ? null : path.join(process.resourcesPath, 'backend-runtime')

  return {
    command: python.command,
    prefixArgs: [...python.prefixArgs, '-m', 'translate_comments.api'],
    cwd,
    extraPath: [
      runtimeRoot,
      runtimeRoot && path.join(runtimeRoot, 'Scripts'),
    ].filter(Boolean),
  }
}

function startBackend () {
  const launcher = resolveBackendLauncher()
  const pathEntries = [
    ...launcher.extraPath,
    process.env.PATH,
  ].filter(Boolean)

  backendProcess = spawn(launcher.command, [...launcher.prefixArgs, '--port', String(API_PORT)], {
    cwd: launcher.cwd,
    env: {
      ...process.env,
      PATH: pathEntries.join(path.delimiter),
      PYTHONUNBUFFERED: '1',
      PYTHONPATH: [launcher.cwd, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    },
  })

  backendProcess.stdout.on('data', d => process.stdout.write(`[backend] ${d}`))
  backendProcess.stderr.on('data', d => process.stderr.write(`[backend] ${d}`))
  backendProcess.on('error', err => {
    console.error(`[backend] failed to start: ${err.message}`)
  })
  backendProcess.on('exit', code => {
    if (code !== 0 && code !== null)
      console.error(`[backend] exited with code ${code}`)
    backendProcess = null
  })
}

// ── Wait until an HTTP server responds ───────────────────────────────────────

function waitForHttp (url, retries = 40, intervalMs = 500) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      http.get(url, res => {
        // Any non-5xx means the server is up
        if (res.statusCode < 500) return resolve()
        res.resume()
        setTimeout(() => attempt(n - 1), intervalMs)
      }).on('error', () => {
        if (n <= 0) return reject(new Error(`Server not ready after ${retries} attempts: ${url}`))
        setTimeout(() => attempt(n - 1), intervalMs)
      })
    }
    attempt(retries)
  })
}

const waitForBackend = () => waitForHttp(`http://127.0.0.1:${API_PORT}/api/health`)
const waitForVite    = () => waitForHttp('http://localhost:5173')

// ── Create window ─────────────────────────────────────────────────────────────

async function createWindow () {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 820,
    minWidth: 1100,
    minHeight: 660,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    backgroundColor: '#0D1117',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  mainWindow.on('closed', () => { mainWindow = null })
}

// ── IPC handlers (native dialogs) ─────────────────────────────────────────────

ipcMain.handle('dialog:openDirectory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'multiSelections'],
  })
  return result.filePaths
})

ipcMain.handle('dialog:openFile', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'C/C++ Source', extensions: ['cpp','cxx','cc','c','h','hpp','hxx','hh','inl','ipp'] },
      { name: 'All Files', extensions: ['*'] },
    ],
  })
  return result.filePaths
})

ipcMain.handle('app:getApiPort', () => API_PORT)

ipcMain.handle('ollama:translateText', async (_event, payload) => {
  const text = String(payload?.text ?? '').trim()
  const host = String(payload?.host ?? 'http://localhost:11434').replace(/\/$/, '')
  const model = String(payload?.model ?? 'qwen2.5:7b')
  const sourceLanguage = String(payload?.sourceLanguage ?? 'Chinese')
  const targetLanguage = String(payload?.targetLanguage ?? 'English')

  if (!text) {
    throw new Error('文本不能为空')
  }

  const response = await fetch(`${host}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      stream: false,
      messages: [
        {
          role: 'system',
          content:
            `You are a precise technical translator. Translate the following text from ${sourceLanguage} to ${targetLanguage}. `
            + 'Output only the translated text. Preserve code identifiers, API names, file paths, and technical terms when appropriate.',
        },
        {
          role: 'user',
          content: `Text to translate:\n${text}`,
        },
      ],
      options: {
        temperature: 0.1,
        num_predict: 768,
      },
    }),
  })

  if (!response.ok) {
    throw new Error(`Ollama 请求失败: HTTP ${response.status}`)
  }

  const data = await response.json()
  const translated = data?.message?.content
  if (typeof translated !== 'string' || !translated.trim()) {
    throw new Error('Ollama 返回了空结果')
  }

  return translated.trim()
})

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  startBackend()

  // Wait for both Vite dev server and Python backend in parallel
  const waits = [
    waitForBackend().catch(e => console.warn('[backend]', e.message)),
  ]
  if (isDev) {
    waits.push(waitForVite().catch(e => console.warn('[vite]', e.message)))
  }
  await Promise.all(waits)

  await createWindow()
})

app.on('window-all-closed', () => {
  if (backendProcess) backendProcess.kill()
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
