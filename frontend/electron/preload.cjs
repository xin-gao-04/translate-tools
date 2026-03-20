const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openFile:      () => ipcRenderer.invoke('dialog:openFile'),
  getApiPort:    () => ipcRenderer.invoke('app:getApiPort'),
  translateText: (payload) => ipcRenderer.invoke('ollama:translateText', payload),
})
