import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type ChatMessage,
  getApiBaseUrl,
  imageUrlsFromSavedFiles,
  runGeneration,
  uploadImage,
} from "./api"
import { loadPersistedState, savePersistedState } from "./chatStorage"
import { ChatWindow } from "./components/ChatWindow"
import { InputBar } from "./components/InputBar"
import { Sidebar } from "./components/Sidebar"
import { readFileAsDataUrl } from "./readFileAsDataUrl"
import { titleFromMessages } from "./titleUtils"
import { DEFAULT_SESSION_TITLE, type ChatSession } from "./types/chatSession"
import "./App.css"

function newId(): string {
  return crypto.randomUUID()
}

function createSession(): ChatSession {
  return {
    id: newId(),
    title: DEFAULT_SESSION_TITLE,
    messages: [],
    updatedAt: Date.now(),
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

export default function App() {
  const baseUrl = getApiBaseUrl()
  const [{ sessions, activeId }, setBoth] = useState(() => initialState())
  const [inputText, setInputText] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const previewUrlRef = useRef<string | null>(null)
  const [uploading, setUploading] = useState(false)
  /** 最近一次成功上傳的檔名（收起預覽後仍顯示「已選：…」） */
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null)
  const [serverReady, setServerReady] = useState(false)
  const [loading, setLoading] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth > 900 : true,
  )

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
    savePersistedState({ sessions, activeId })
  }, [sessions, activeId])

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeId),
    [sessions, activeId],
  )
  const messages = activeSession?.messages ?? []

  const patchActiveMessages = useCallback(
    (fn: (prev: ChatMessage[]) => ChatMessage[]) => {
      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== activeId) return s
          const nextMessages = fn(s.messages)
          const title = titleFromMessages(nextMessages, s.title)
          return {
            ...s,
            messages: nextMessages,
            title,
            updatedAt: Date.now(),
          }
        }),
      )
    },
    [activeId, setSessions],
  )

  const revokePreview = useCallback(() => {
    const prev = previewUrlRef.current
    if (prev && prev.startsWith("blob:")) {
      URL.revokeObjectURL(prev)
    }
    previewUrlRef.current = null
    setPreviewUrl(null)
  }, [])

  const resetInputAndUpload = useCallback(() => {
    setInputText("")
    revokePreview()
    setFile(null)
    setUploadedFileName(null)
    setServerReady(false)
  }, [revokePreview])

  const handleNewChat = useCallback(() => {
    const s = createSession()
    setSessions((prev) => [s, ...prev])
    setActiveIdOnly(s.id)
    resetInputAndUpload()
  }, [resetInputAndUpload, setActiveIdOnly, setSessions])

  const handleSelectChat = useCallback(
    (id: string) => {
      setActiveIdOnly(id)
      resetInputAndUpload()
    },
    [resetInputAndUpload, setActiveIdOnly],
  )

  const handleDeleteChat = useCallback((id: string) => {
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
  }, [])

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
      if (!next) {
        revokePreview()
        setFile(null)
        setUploadedFileName(null)
        return
      }

      revokePreview()
      setFile(next)
      const url = URL.createObjectURL(next)
      previewUrlRef.current = url
      setPreviewUrl(url)
      setUploading(true)
      try {
        await uploadImage(next, baseUrl)
        setUploadedFileName(next.name)
        setServerReady(true)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        revokePreview()
        setFile(null)
        setUploadedFileName(null)
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
    [baseUrl, patchActiveMessages, revokePreview],
  )

  const handleSend = useCallback(async () => {
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

    let imageSnapshot: string | undefined
    if (file) {
      try {
        imageSnapshot = await readFileAsDataUrl(file)
      } catch {
        imageSnapshot = undefined
      }
    }

    const userMsg: ChatMessage = {
      id: newId(),
      role: "user",
      text: text || "（僅圖片）",
      imagePreview: imageSnapshot,
    }
    patchActiveMessages((m) => [...m, userMsg])
    setInputText("")
    revokePreview()
    setFile(null)
    setLoading(true)

    try {
      const result = await runGeneration(text, baseUrl)
      const urls = imageUrlsFromSavedFiles(result.saved_files ?? [], baseUrl)
      const assistantText =
        urls.length > 0
          ? "圖片生成完成，請檢視下方結果。"
          : "流程已結束，但未取得產出圖片（請檢查後端日誌或設定）。"

      patchActiveMessages((m) => [
        ...m,
        {
          id: newId(),
          role: "assistant",
          text: assistantText,
          generatedImages: urls.length > 0 ? urls : undefined,
        },
      ])
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      patchActiveMessages((m) => [
        ...m,
        {
          id: newId(),
          role: "assistant",
          text: `執行失敗：${msg}`,
          error: true,
        },
      ])
    } finally {
      setLoading(false)
    }
  }, [
    baseUrl,
    file,
    serverReady,
    inputText,
    patchActiveMessages,
    revokePreview,
  ])

  const busy = loading || uploading

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
          onSelect={handleSelectChat}
          onRename={handleRenameSession}
          onDelete={handleDeleteChat}
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
            <div className="app-header-titles">
              <h1 className="app-title">AI 電商圖文助手</h1>
              <p className="app-sub">API：{baseUrl}</p>
            </div>
          </header>
          <main className="app-main">
            <ChatWindow messages={messages} loading={loading} />
            <InputBar
              value={inputText}
              onChange={setInputText}
              onSend={handleSend}
              disabled={busy}
              file={file}
              uploadedFileName={uploadedFileName}
              previewUrl={previewUrl}
              onFileChange={handleFileChange}
              uploading={uploading}
            />
          </main>
        </div>
      </div>
    </div>
  )
}
