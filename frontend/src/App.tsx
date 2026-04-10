import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type ChatMessage,
  consumeRunStream,
  deleteSessionUpload,
  deleteSessionUploadImage,
  fetchSessionState,
  getApiBaseUrl,
  imageUrlsFromSavedFiles,
  saveSessionState,
  uploadImage,
} from "./api"
import { loadPersistedState, savePersistedState } from "./chatStorage"
import { ChatWindow } from "./components/ChatWindow"
import { InputBar } from "./components/InputBar"
import { SettingsPage } from "./components/SettingsPage"
import { Sidebar } from "./components/Sidebar"
import { readFileAsDataUrl } from "./readFileAsDataUrl"
import { getToolById } from "./tools"
import { titleFromMessages } from "./titleUtils"
import { DEFAULT_SESSION_TITLE, type ChatSession } from "./types/chatSession"
import { useObjectUrlForFile } from "./useObjectUrlForFile"
import "./App.css"

function newId(): string {
  return crypto.randomUUID()
}

function createSession(toolId?: string): ChatSession {
  return {
    id: newId(),
    title: DEFAULT_SESSION_TITLE,
    messages: [],
    updatedAt: Date.now(),
    isRunning: false,
    streamPrimed: false,
    taskCompleted: false,
    clearOnNextSend: false,
    toolId,
  }
}

function initialState(): { sessions: ChatSession[]; activeId: string } {
  const persisted = loadPersistedState()
  if (persisted?.sessions?.length) {
    const activeOk = persisted.sessions.some((s) => s.id === persisted.activeId)
    return {
      sessions: persisted.sessions,
      activeId: activeOk ? persisted.activeId : persisted.sessions[0].id,
    }
  }
  const s = createSession()
  return { sessions: [s], activeId: s.id }
}

async function cloneFileDetached(file: File): Promise<File> {
  const buf = await file.arrayBuffer()
  return new File([buf], file.name, {
    type: file.type || "application/octet-stream",
    lastModified: Date.now(),
  })
}

export default function App() {
  const baseUrl = getApiBaseUrl()
  const [{ sessions, activeId }, setBoth] = useState(() => initialState())
  const [inputText, setInputText] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const fileObjectUrl = useObjectUrlForFile(file)
  const [uploading, setUploading] = useState(false)
  /** 最近一次成功上傳的檔名（收起預覽後仍顯示「已選：…」） */
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null)
  /** 上傳成功後保留在輸入區的預覽（data URL），直到送出或移除；不受 blob revoke 影響 */
  const [inputPreviewDataUrl, setInputPreviewDataUrl] = useState<string | null>(
    null,
  )
  /** 從「本次成功上傳」到「送出」之間為 true，用後端 sample 圖做預覽後備（並在送出後關閉） */
  const [inputPreviewActive, setInputPreviewActive] = useState(false)
  const [samplePreviewEpoch, setSamplePreviewEpoch] = useState(0)
  const [serverReady, setServerReady] = useState(false)
  const [hydratedFromServer, setHydratedFromServer] = useState(false)
  const runControllersRef = useRef<Map<string, AbortController>>(new Map())
  /** 由 ChatWindow 註冊：切換／新建對話前先寫入目前訊息區捲動位置 */
  const messagesScrollFlushRef = useRef<() => void>(() => {})
  const messagesScrollPersistTimerRef = useRef<ReturnType<
    typeof setTimeout
  > | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth > 900 : true,
  )
  const [mainView, setMainView] = useState<"chat" | "settings">("chat")
  /**
   * 點擊工具後建立的「暫存對話」，尚未加入 sessions。
   * 送出第一筆訊息時才正式 commit 進 sessions。
   */
  const [pendingToolSession, setPendingToolSession] = useState<ChatSession | null>(null)

  const setSessions = useCallback(
    (updater: ChatSession[] | ((prev: ChatSession[]) => ChatSession[])) => {
      setBoth((prev) => {
        const next =
          typeof updater === "function"
            ? updater(prev.sessions)
            : updater
        return { ...prev, sessions: next }
      })
    },
    [],
  )

  const setActiveIdOnly = useCallback((id: string) => {
    setBoth((prev) => ({ ...prev, activeId: id }))
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const remote = await fetchSessionState(baseUrl)
        if (!remote || cancelled) return
        setBoth((prev) => {
          const remoteSessions = remote.sessions as ChatSession[]
          if (!remoteSessions.length) return prev
          const activeOk = remoteSessions.some((s) => s.id === remote.activeId)
          return {
            sessions: remoteSessions,
            activeId: activeOk ? remote.activeId : remoteSessions[0].id,
          }
        })
      } catch {
        // 後端無法載入時，回退本地 localStorage
      } finally {
        if (!cancelled) setHydratedFromServer(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [baseUrl])

  useEffect(() => {
    savePersistedState({ sessions, activeId })
    if (!hydratedFromServer) return
    void saveSessionState(baseUrl, { sessions, activeId }).catch(() => {
      // 後端暫時不可用時保留本地狀態，不阻斷操作
    })
  }, [activeId, baseUrl, hydratedFromServer, sessions])

  const activeSession = useMemo(() => {
    if (pendingToolSession && pendingToolSession.id === activeId) {
      return pendingToolSession
    }
    return sessions.find((s) => s.id === activeId)
  }, [sessions, activeId, pendingToolSession])
  const messages = activeSession?.messages ?? []
  const activeStreaming = activeSession?.isRunning ?? false
  const activeStreamPrimed = activeSession?.streamPrimed ?? false
  const taskCompleted = activeSession?.taskCompleted ?? false
  const clearOnNextSend = activeSession?.clearOnNextSend ?? false

  const fallbackSamplePreviewUrl = useMemo(() => {
    if (!serverReady) return null
    // 本地仍有 File 時由 blob URL 顯示，避免在 object URL 就緒前閃爍後端舊圖
    if (file) return null
    if (!uploadedFileName) return null
    if (fileObjectUrl || inputPreviewDataUrl) return null
    if (!inputPreviewActive) return null
    const base = baseUrl.replace(/\/+$/, "")
    return `${base}/sample-reference?session_id=${encodeURIComponent(activeId)}&v=${samplePreviewEpoch}`
  }, [
    activeId,
    baseUrl,
    file,
    fileObjectUrl,
    inputPreviewActive,
    inputPreviewDataUrl,
    samplePreviewEpoch,
    serverReady,
    uploadedFileName,
  ])

  const patchActiveSession = useCallback(
    (fn: (session: ChatSession) => ChatSession) => {
      setPendingToolSession((p) => (p && p.id === activeId ? fn(p) : p))
      setSessions((prev) =>
        prev.map((s) => (s.id === activeId ? fn(s) : s)),
      )
    },
    [activeId, setSessions],
  )

  const patchSession = useCallback(
    (sessionId: string, fn: (session: ChatSession) => ChatSession) => {
      setPendingToolSession((p) => (p && p.id === sessionId ? fn(p) : p))
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? fn(s) : s)),
      )
    },
    [setSessions],
  )

  const patchSessionMessages = useCallback(
    (sessionId: string, fn: (prev: ChatMessage[]) => ChatMessage[]) => {
      const applyMsgs = (s: ChatSession): ChatSession => {
        const nextMessages = fn(s.messages)
        const title = titleFromMessages(nextMessages, s.title)
        return { ...s, messages: nextMessages, title, updatedAt: Date.now() }
      }
      setPendingToolSession((p) =>
        p && p.id === sessionId ? applyMsgs(p) : p,
      )
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? applyMsgs(s) : s)),
      )
    },
    [setSessions],
  )

  const persistMessagesScroll = useCallback(
    (sessionId: string, scrollTop: number) => {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? { ...s, messagesScrollTop: scrollTop } : s,
        ),
      )
    },
    [setSessions],
  )

  const scheduleMessagesScrollPersist = useCallback(
    (sessionId: string, scrollTop: number) => {
      if (messagesScrollPersistTimerRef.current) {
        clearTimeout(messagesScrollPersistTimerRef.current)
      }
      messagesScrollPersistTimerRef.current = setTimeout(() => {
        messagesScrollPersistTimerRef.current = null
        persistMessagesScroll(sessionId, scrollTop)
      }, 400)
    },
    [persistMessagesScroll],
  )

  const flushMessagesScroll = useCallback(() => {
    if (messagesScrollPersistTimerRef.current) {
      clearTimeout(messagesScrollPersistTimerRef.current)
      messagesScrollPersistTimerRef.current = null
    }
    messagesScrollFlushRef.current()
  }, [])

  const patchActiveMessages = useCallback(
    (fn: (prev: ChatMessage[]) => ChatMessage[]) => {
      const applyMsgs = (s: ChatSession): ChatSession => {
        const nextMessages = fn(s.messages)
        const title = titleFromMessages(nextMessages, s.title)
        return { ...s, messages: nextMessages, title, updatedAt: Date.now() }
      }
      setPendingToolSession((p) => (p && p.id === activeId ? applyMsgs(p) : p))
      setSessions((prev) =>
        prev.map((s) => (s.id === activeId ? applyMsgs(s) : s)),
      )
    },
    [activeId, setSessions],
  )

  const resetInputAndUpload = useCallback(() => {
    setInputText("")
    setFile(null)
    setUploadedFileName(null)
    setInputPreviewDataUrl(null)
    setInputPreviewActive(false)
    setServerReady(false)
  }, [])

  const handleNewChat = useCallback(() => {
    setMainView("chat")
    flushMessagesScroll()
    setPendingToolSession(null)
    const s = createSession()
    setSessions((prev) => [s, ...prev])
    setActiveIdOnly(s.id)
    resetInputAndUpload()
  }, [flushMessagesScroll, resetInputAndUpload, setActiveIdOnly, setSessions])

  const handleNewToolChat = useCallback(
    (toolId: string) => {
      setMainView("chat")
      flushMessagesScroll()
      const s = createSession(toolId)
      // 暫存，不加入 sessions；等送出第一筆訊息時才 commit
      setPendingToolSession(s)
      setActiveIdOnly(s.id)
      resetInputAndUpload()
    },
    [flushMessagesScroll, resetInputAndUpload, setActiveIdOnly],
  )

  const handleSelectChat = useCallback(
    (id: string) => {
      setMainView("chat")
      flushMessagesScroll()
      setPendingToolSession(null)
      setActiveIdOnly(id)
      resetInputAndUpload()
    },
    [flushMessagesScroll, resetInputAndUpload, setActiveIdOnly],
  )

  const handleDeleteChat = useCallback((id: string) => {
    flushMessagesScroll()
    runControllersRef.current.get(id)?.abort()
    runControllersRef.current.delete(id)
    void deleteSessionUpload(id, baseUrl).catch(() => {
      // 刪除對話時若後端檔案刪除失敗，不阻斷前端對話刪除流程
    })
    setBoth((prev) => {
      const nextSessions = prev.sessions.filter((s) => s.id !== id)
      if (nextSessions.length === 0) {
        const fresh = createSession()
        return { sessions: [fresh], activeId: fresh.id }
      }
      const nextActive =
        prev.activeId === id ? nextSessions[0].id : prev.activeId
      return { sessions: nextSessions, activeId: nextActive }
    })
  }, [baseUrl, flushMessagesScroll])

  const handleRenameSession = useCallback(
    (id: string, newTitle: string) => {
      const t = newTitle.trim() || DEFAULT_SESSION_TITLE
      setSessions((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, title: t, updatedAt: Date.now() } : s,
        ),
      )
    },
    [setSessions],
  )

  const handleFileChange = useCallback(
    async (next: File | null) => {
      if (taskCompleted) {
        return
      }
      if (!next) {
        setFile(null)
        setUploadedFileName(null)
        setInputPreviewDataUrl(null)
        setInputPreviewActive(false)
        void deleteSessionUploadImage(activeId, baseUrl).catch(() => {
          // 使用者手動移除預覽時，後端刪檔失敗不阻斷前端操作
        })
        return
      }

      const detached = await cloneFileDetached(next)
      setInputPreviewDataUrl(null)
      setInputPreviewActive(false)
      setFile(detached)
      setUploading(true)
      try {
        await uploadImage(detached, baseUrl, activeId)
        setUploadedFileName(detached.name)
        setServerReady(true)
        setInputPreviewActive(true)
        setSamplePreviewEpoch((e) => e + 1)
        try {
          const dataUrl = await readFileAsDataUrl(detached)
          setInputPreviewDataUrl(dataUrl)
        } catch {
          setInputPreviewDataUrl(null)
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        setFile(null)
        setUploadedFileName(null)
        setInputPreviewDataUrl(null)
        setInputPreviewActive(false)
        patchActiveMessages((m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            text: `圖片上傳失敗：${msg}`,
            error: true,
          },
        ])
        setServerReady(false)
      } finally {
        setUploading(false)
      }
    },
    [activeId, baseUrl, patchActiveMessages, taskCompleted],
  )

  const handleSend = useCallback(async () => {
    if (taskCompleted) {
      return
    }
    const text = inputText.trim()
    if (!serverReady) {
      patchActiveMessages((m) => [
        ...m,
        {
          id: newId(),
          role: "assistant",
          text: "請先使用 📎 上傳一張商品圖片，成功後再送出。",
          error: true,
        },
      ])
      return
    }

    // 若為暫存工具對話，送出時才正式加入 sessions
    if (pendingToolSession && pendingToolSession.id === activeId) {
      setSessions((prev) => [pendingToolSession, ...prev])
      setPendingToolSession(null)
    }

    let imageSnapshot: string | undefined
    if (file) {
      try {
        imageSnapshot = await readFileAsDataUrl(file)
      } catch {
        imageSnapshot = undefined
      }
    }
    if (!imageSnapshot && inputPreviewDataUrl) {
      imageSnapshot = inputPreviewDataUrl
    }

    if (clearOnNextSend) {
      patchActiveMessages(() => [])
      patchActiveSession((s) => ({
        ...s,
        clearOnNextSend: false,
        updatedAt: Date.now(),
      }))
    }

    const userMsg: ChatMessage = {
      id: newId(),
      role: "user",
      text: text || "（僅圖片）",
      imagePreview: imageSnapshot,
    }
    patchActiveMessages((m) => [...m, userMsg])
    setInputText("")
    setFile(null)
    setUploadedFileName(null)
    setInputPreviewDataUrl(null)
    setInputPreviewActive(false)
    const sessionId = activeId
    patchSession(sessionId, (s) => ({
      ...s,
      isRunning: true,
      streamPrimed: false,
      updatedAt: Date.now(),
    }))
    const aborter = new AbortController()
    runControllersRef.current.set(sessionId, aborter)

    try {
      await consumeRunStream(text, baseUrl, (ev) => {
        patchSession(sessionId, (s) => ({
          ...s,
          streamPrimed: true,
          updatedAt: Date.now(),
        }))
        if (ev.type === "collapsible_init") {
          patchSessionMessages(sessionId, (m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              collapsible: {
                id: ev.group_id,
                title: ev.title,
                lines: [],
              },
            },
          ])
          return
        }
        if (ev.type === "collapsible_line") {
          patchSessionMessages(sessionId, (m) =>
            m.map((msg) => {
              if (msg.collapsible?.id !== ev.group_id) return msg
              return {
                ...msg,
                collapsible: {
                  ...msg.collapsible,
                  lines: [...msg.collapsible.lines, ev.line],
                },
              }
            }),
          )
          return
        }
        if (ev.type === "text_block") {
          const fmt =
            ev.format === "markdown" || ev.format === "json"
              ? "markdown"
              : "plain"
          patchSessionMessages(sessionId, (m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              text: ev.content,
              textFormat: fmt,
            },
          ])
          return
        }
        if (ev.type === "image_saved") {
          const label = `P${Number(ev.sort).toString().padStart(2, "0")}`
          const urls = imageUrlsFromSavedFiles([ev.saved_file], baseUrl)
          patchSessionMessages(sessionId, (m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              text: `**${label}** ${ev.main}`,
              textFormat: "markdown",
              generatedImages: urls,
            },
          ])
          return
        }
        if (ev.type === "complete") {
          patchSession(sessionId, (s) => ({
            ...s,
            isRunning: false,
            streamPrimed: false,
            taskCompleted: true,
            updatedAt: Date.now(),
          }))
          return
        }
        if (ev.type === "error") {
          patchSessionMessages(sessionId, (m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              text: `執行失敗：${ev.detail}`,
              error: true,
            },
          ])
        }
      }, aborter.signal, sessionId)
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        patchSessionMessages(sessionId, (m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            text: "已停止目前流程。",
          },
        ])
        patchSession(sessionId, (s) => ({
          ...s,
          isRunning: false,
          streamPrimed: false,
          clearOnNextSend: true,
          updatedAt: Date.now(),
        }))
        return
      }
      const msg = e instanceof Error ? e.message : String(e)
      patchSessionMessages(sessionId, (m) => [
        ...m,
        {
          id: newId(),
          role: "assistant",
          text: `執行失敗：${msg}`,
          error: true,
        },
      ])
    } finally {
      if (runControllersRef.current.get(sessionId) === aborter) {
        runControllersRef.current.delete(sessionId)
      }
      patchSession(sessionId, (s) => ({
        ...s,
        isRunning: false,
        streamPrimed: false,
        updatedAt: Date.now(),
      }))
    }
  }, [
    activeId,
    baseUrl,
    file,
    inputPreviewDataUrl,
    serverReady,
    clearOnNextSend,
    inputText,
    pendingToolSession,
    setSessions,
    patchActiveSession,
    patchActiveMessages,
    patchSession,
    patchSessionMessages,
    taskCompleted,
  ])

  const busy = activeStreaming || uploading
  const handleStop = useCallback(() => {
    runControllersRef.current.get(activeId)?.abort()
  }, [activeId])

  return (
    <div className="app-root">
      <div className={`app-shell ${sidebarOpen ? "app-shell--sidebar-open" : ""}`}>
        <div
          className="sidebar-backdrop"
          aria-hidden={!sidebarOpen}
          onClick={() => setSidebarOpen(false)}
        />
        <Sidebar
          sessions={sessions}
          activeId={activeId}
          onNewChat={handleNewChat}
          onNewToolChat={handleNewToolChat}
          onSelect={handleSelectChat}
          onRename={handleRenameSession}
          onDelete={handleDeleteChat}
          settingsActive={mainView === "settings"}
          onOpenSettings={() => setMainView("settings")}
          onNavigate={() => {
            if (typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches) {
              setSidebarOpen(false)
            }
          }}
        />
        <div className="app-chat-column">
          <header className="app-header">
            <button
              type="button"
              className="app-header-menu"
              aria-label="開啟或收合側邊欄"
              onClick={() => setSidebarOpen((o) => !o)}
            >
              ☰
            </button>
            {mainView === "settings" ? (
              <button
                type="button"
                className="app-header-back"
                onClick={() => setMainView("chat")}
              >
                ← 返回聊天
              </button>
            ) : null}
            <div className="app-header-titles">
              <h1 className="app-title">
                {mainView === "settings"
                  ? "設定與說明"
                  : activeSession?.toolId
                    ? (getToolById(activeSession.toolId)?.chatTitle ?? "AI 助手")
                    : "AI 電商圖文助手"}
              </h1>
              <p className="app-sub">API：{baseUrl}</p>
            </div>
          </header>
          <main
            className={
              mainView === "settings"
                ? "app-main app-main--settings"
                : "app-main app-main--chat"
            }
          >
            {mainView === "settings" ? (
              <SettingsPage baseUrl={baseUrl} />
            ) : (
              <>
                <ChatWindow
                  sessionId={activeId}
                  savedScrollTop={activeSession?.messagesScrollTop}
                  scheduleScrollTopPersist={scheduleMessagesScrollPersist}
                  persistScrollTopNow={persistMessagesScroll}
                  scrollFlushRef={messagesScrollFlushRef}
                  messages={messages}
                  streaming={activeStreaming}
                  streamPrimed={activeStreamPrimed}
                  toolId={activeSession?.toolId}
                />
                <InputBar
                  value={inputText}
                  onChange={setInputText}
                  onSend={handleSend}
                  onStop={handleStop}
                  disabled={busy}
                  isStreaming={activeStreaming}
                  taskCompleted={taskCompleted}
                  file={file}
                  uploadedFileName={uploadedFileName}
                  previewUrl={fileObjectUrl}
                  inputPreviewDataUrl={inputPreviewDataUrl}
                  fallbackSamplePreviewUrl={fallbackSamplePreviewUrl}
                  onFileChange={handleFileChange}
                  uploading={uploading}
                />
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}
