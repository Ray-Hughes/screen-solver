const { contextBridge, ipcRenderer } = require("electron");

// A narrow surface. The dashboard talks to the Python backend over HTTP just
// as it does in a browser; this exposes only what a web page cannot do —
// notably screen capture, which must happen in this bundle so that macOS
// attributes Screen Recording to Screen Solver rather than to a child process.
contextBridge.exposeInMainWorld("solverDesktop", {
  isDesktop: true,
  platform: process.platform,
  hotkeys: {
    capture: "⌥⌘C",
    captureSolve: "⌥⌘S",
    toggleWatch: "⌥⌘W",
    exploreSolve: "⌥⌘E",
    addSupport: "⌥⌘A",
    toggleWindow: "⌥⌘D",
  },
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  capture: (opts) => ipcRenderer.invoke("capture", opts),
  setWatch: (cfg) => ipcRenderer.invoke("set-watch", cfg),
  setDisplay: (index) => ipcRenderer.invoke("set-display", index),
  explore: (opts) => ipcRenderer.invoke("explore", opts),
  addSupport: (opts) => ipcRenderer.invoke("add-support", opts),
  screenPermission: () => ipcRenderer.invoke("screen-permission"),
  requestScreenAccess: () => ipcRenderer.invoke("request-screen-access"),
  onWatch: (fn) => ipcRenderer.on("watch", (_e, d) => fn(d)),
  onCaptureError: (fn) => ipcRenderer.on("capture-error", (_e, d) => fn(d)),
  onExploreError: (fn) => ipcRenderer.on("explore-error", (_e, d) => fn(d)),
  onCommand: (fn) => ipcRenderer.on("command", (_e, name) => fn(name)),
});
