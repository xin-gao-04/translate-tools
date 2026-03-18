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
const path = require('path')
const { spawn } = require('child_process')
const http = require('http')

const isDev = process.argv.includes('--dev')
const API_PORT = 8765

let mainWindow = null
let backendProcess = null

// ── Start Python backend ──────────────────────────────────────────────────────

function startBackend () {
  const pythonCmd = isDev
    ? ['-m', 'translate_comments.api', '--port', String(API_PORT)]
    : null  // TODO: point to PyInstaller bundle in production

  if (!pythonCmd) return   // production bundling handled separately

  const exe = process.platform === 'win32' ? 'python' : 'python3'

  // In dev, run from the project root (one level up from frontend/)
  const cwd = isDev
    ? path.join(__dirname, '..', '..')  // translate-tool/
    : path.join(process.resourcesPath, 'backend')

  backendProcess = spawn(exe, pythonCmd, {
    cwd,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  })

  backendProcess.stdout.on('data', d => process.stdout.write(`[backend] ${d}`))
  backendProcess.stderr.on('data', d => process.stderr.write(`[backend] ${d}`))
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
