import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type ChatMessage,
  consumeRunStream,
  getApiBaseUrl,
  imageUrlsFromSavedFiles,
  uploadImage,
} from "./api"
import { loadPersistedState, savePersistedState } from "./chatStorage"
import { ChatWindow } from "./components/ChatWindow"
import { InputBar } from "./components/InputBar"
import { Sidebar } from "./components/Sidebar"
import { readFileAsDataUrl } from "./readFileAsDataUrl"
import { titleFromMessages } from "./titleUtils"
import { DEFAULT_SESSION_TITLE, type ChatSession } from "./types/chatSession"
import { useObjectUrlForFile } from "./useObjectUrlForFile"
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
  const [streaming, setStreaming] = useState(false)
  const [streamPrimed, setStreamPrimed] = useState(false)
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

  const fallbackSamplePreviewUrl = useMemo(() => {
    if (!serverReady) return null
    // 本地仍有 File 時由 blob URL 顯示，避免在 object URL 就緒前閃爍後端舊圖
    if (file) return null
    if (!uploadedFileName) return null
    if (fileObjectUrl || inputPreviewDataUrl) return null
    if (!inputPreviewActive) return null
    const base = baseUrl.replace(/\/+$/, "")
    return `${base}/sample-reference?v=${samplePreviewEpoch}`
  }, [
    baseUrl,
    file,
    fileObjectUrl,
    inputPreviewActive,
    inputPreviewDataUrl,
    samplePreviewEpoch,
    serverReady,
    uploadedFileName,
  ])

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

  const resetInputAndUpload = useCallback(() => {
    setInputText("")
    setFile(null)
    setUploadedFileName(null)
    setInputPreviewDataUrl(null)
    setInputPreviewActive(false)
    setServerReady(false)
  }, [])

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
        setFile(null)
        setUploadedFileName(null)
        setInputPreviewDataUrl(null)
        setInputPreviewActive(false)
        return
      }

      const detached = await cloneFileDetached(next)
      setInputPreviewDataUrl(null)
      setInputPreviewActive(false)
      setFile(detached)
      setUploading(true)
      try {
        await uploadImage(detached, baseUrl)
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
    [baseUrl, patchActiveMessages],
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
    if (!imageSnapshot && inputPreviewDataUrl) {
      imageSnapshot = inputPreviewDataUrl
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
    setStreaming(true)
    setStreamPrimed(false)

    try {
      await consumeRunStream(text, baseUrl, (ev) => {
        setStreamPrimed(true)
        if (ev.type === "collapsible_init") {
          patchActiveMessages((m) => [
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
          patchActiveMessages((m) =>
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
          patchActiveMessages((m) => [
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
        if (ev.type === "complete") {
          const urls = imageUrlsFromSavedFiles(ev.saved_files ?? [], baseUrl)
          patchActiveMessages((m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              text:
                urls.length > 0
                  ? "圖片生成完成，請檢視下方結果。"
                  : "流程已結束，但未取得產出圖片（請檢查後端日誌或設定）。",
              generatedImages: urls.length > 0 ? urls : undefined,
            },
          ])
          return
        }
        if (ev.type === "error") {
          patchActiveMessages((m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              text: `執行失敗：${ev.detail}`,
              error: true,
            },
          ])
        }
      })
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
      setStreaming(false)
      setStreamPrimed(false)
    }
  }, [
    baseUrl,
    file,
    inputPreviewDataUrl,
    serverReady,
    inputText,
    patchActiveMessages,
  ])

  const busy = streaming || uploading

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
            <ChatWindow
              messages={messages}
              streaming={streaming}
              streamPrimed={streamPrimed}
            />
            <InputBar
              value={inputText}
              onChange={setInputText}
              onSend={handleSend}
              disabled={busy}
              file={file}
              uploadedFileName={uploadedFileName}
              previewUrl={fileObjectUrl}
              inputPreviewDataUrl={inputPreviewDataUrl}
              fallbackSamplePreviewUrl={fallbackSamplePreviewUrl}
              onFileChange={handleFileChange}
              uploading={uploading}
            />
          </main>
        </div>
      </div>
    </div>
  )
}
