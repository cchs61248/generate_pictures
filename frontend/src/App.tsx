import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { flushSync } from "react-dom"
import {
  type ChatMessage,
  consumeImageThreadStream,
  consumeRunStream,
  deleteSessionUpload,
  deleteSessionUploadImage,
  fetchSessionState,
  getApiBaseUrl,
  imageUrlsFromSavedFiles,
  initImageThread,
  saveSessionState,
  uploadImage,
} from "./api"
import { loadPersistedState, savePersistedState } from "./chatStorage"
import { ChatWindow } from "./components/ChatWindow"
import { InputBar } from "./components/InputBar"
import { SettingsPage } from "./components/SettingsPage"
import { Sidebar } from "./components/Sidebar"
import { readFileAsDataUrl } from "./readFileAsDataUrl"
import { getToolById, TOOLS } from "./tools"
import { titleFromMessages } from "./titleUtils"
import { DEFAULT_SESSION_TITLE, type ChatSession } from "./types/chatSession"
import { useObjectUrlForFile } from "./useObjectUrlForFile"
import "./App.css"

const STORAGE_KEY = "gnerate_pictures_chat_v1"

function newId(): string {
  return crypto.randomUUID()
}

function createSession(toolId?: string): ChatSession {
  const toolTitle = toolId ? getToolById(toolId)?.chatTitle : undefined
  return {
    id: newId(),
    title: toolTitle ?? DEFAULT_SESSION_TITLE,
    messages: [],
    updatedAt: Date.now(),
    isRunning: false,
    streamPrimed: false,
    taskCompleted: false,
    clearOnNextSend: false,
    toolId,
  }
}

function normalizeBubbleTitle(raw: string): string {
  const t = raw.replace(/\*\*/g, "").replace(/\s+/g, " ").trim()
  return t || "圖片討論串"
}

function pictureFilenameFromImageUrl(imageUrl: string): string | null {
  try {
    const u = new URL(imageUrl)
    const marker = "/images/"
    const idx = u.pathname.lastIndexOf(marker)
    if (idx < 0) return null
    const part = u.pathname.slice(idx + marker.length)
    if (!part) return null
    const name = decodeURIComponent(part.split("/").pop() ?? "")
    return name || null
  } catch {
    return null
  }
}

function insertChildSession(
  prev: ChatSession[],
  parentId: string,
  child: ChatSession,
): ChatSession[] {
  const parentIdx = prev.findIndex((s) => s.id === parentId)
  if (parentIdx < 0) return [child, ...prev]
  let insertAt = parentIdx + 1
  while (insertAt < prev.length && prev[insertAt].parentId === parentId) {
    insertAt += 1
  }
  return [...prev.slice(0, insertAt), child, ...prev.slice(insertAt)]
}

/** 與 409 合併相同：兩邊都有的 id 取 updatedAt 較新者，僅本地有的 id 保留 */
function mergeSessionLists(
  localSessions: ChatSession[],
  remoteSessions: ChatSession[],
  tombstones: Set<string>,
): ChatSession[] {
  const remoteFiltered = remoteSessions.filter((s) => !tombstones.has(s.id))
  const localFiltered = localSessions.filter((s) => !tombstones.has(s.id))
  const remoteMap = new Map(remoteFiltered.map((s) => [s.id, s]))
  const localMap = new Map(localFiltered.map((s) => [s.id, s]))
  const merged: ChatSession[] = []
  for (const [, remoteSess] of remoteMap) {
    const local = localMap.get(remoteSess.id)
    merged.push(
      local && (local.updatedAt ?? 0) >= (remoteSess.updatedAt ?? 0)
        ? local
        : remoteSess,
    )
  }
  for (const [id, local] of localMap) {
    if (!remoteMap.has(id)) merged.push(local)
  }
  return merged
}

type SessionComposerState = {
  inputText: string
  file: File | null
  uploadedFileName: string | null
  inputPreviewDataUrl: string | null
  inputPreviewActive: boolean
  serverReady: boolean
  samplePreviewEpoch: number
}

function emptyComposerState(): SessionComposerState {
  return {
    inputText: "",
    file: null,
    uploadedFileName: null,
    inputPreviewDataUrl: null,
    inputPreviewActive: false,
    serverReady: false,
    samplePreviewEpoch: 0,
  }
}

function createDefaultTemporaryToolSession(): ChatSession {
  const defaultToolId = TOOLS[0]?.id
  return createSession(defaultToolId)
}

function initialState(): {
  sessions: ChatSession[]
  activeId: string
  pendingToolSession: ChatSession | null
} {
  const persisted = loadPersistedState()
  if (persisted?.sessions?.length) {
    const activeOk = persisted.sessions.some((s) => s.id === persisted.activeId)
    return {
      sessions: persisted.sessions,
      activeId: activeOk ? persisted.activeId : persisted.sessions[0].id,
      pendingToolSession: null,
    }
  }
  const temp = createDefaultTemporaryToolSession()
  return { sessions: [], activeId: temp.id, pendingToolSession: temp }
}

function normalizeIncomingState(incoming: {
  sessions: ChatSession[]
  activeId: string
}): {
  sessions: ChatSession[]
  activeId: string
  pendingToolSession: ChatSession | null
} {
  if (incoming.sessions.length === 0) {
    const temp = createDefaultTemporaryToolSession()
    return { sessions: [], activeId: temp.id, pendingToolSession: temp }
  }
  const activeOk = incoming.sessions.some((s) => s.id === incoming.activeId)
  return {
    sessions: incoming.sessions,
    activeId: activeOk ? incoming.activeId : incoming.sessions[0].id,
    pendingToolSession: null,
  }
}

async function cloneFileDetached(file: File): Promise<File> {
  const buf = await file.arrayBuffer()
  return new File([buf], file.name, {
    type: file.type || "application/octet-stream",
    lastModified: Date.now(),
  })
}

export default function App() {
  const boot = initialState()
  const baseUrl = getApiBaseUrl()
  const [{ sessions, activeId }, setBoth] = useState(() => ({
    sessions: boot.sessions,
    activeId: boot.activeId,
  }))
  const [inputText, setInputText] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const fileObjectUrl = useObjectUrlForFile(file)
  const [uploading, setUploading] = useState(false)
  /** 最近一次成功上傳的檔名（收起預覽後仍顯示「已選：…」） */
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null)
  /** 目前輸入區預覽所屬 session；避免切換對話時誤顯示其他對話的預覽 */
  const [inputAssetSessionId, setInputAssetSessionId] = useState<string | null>(
    null,
  )
  /** 上傳成功後保留在輸入區的預覽（data URL），直到送出或移除；不受 blob revoke 影響 */
  const [inputPreviewDataUrl, setInputPreviewDataUrl] = useState<string | null>(
    null,
  )
  /** 從「本次成功上傳」到「送出」之間為 true，用後端 sample 圖做預覽後備（並在送出後關閉） */
  const [inputPreviewActive, setInputPreviewActive] = useState(false)
  const [samplePreviewEpoch, setSamplePreviewEpoch] = useState(0)
  const [serverReady, setServerReady] = useState(false)
  const [, setThreadReferenceMissing] = useState(false)
  const [hydratedFromServer, setHydratedFromServer] = useState(false)
  const sessionVersionRef = useRef(0)
  /** 已刪除的 session ID 墓碑（只累加），確保跨瀏覽器同步刪除 */
  const deletedIdsRef = useRef<string[]>([])
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
  const [pendingToolSession, setPendingToolSession] = useState<ChatSession | null>(
    () => boot.pendingToolSession,
  )
  const pendingToolSessionRef = useRef<ChatSession | null>(pendingToolSession)
  pendingToolSessionRef.current = pendingToolSession

  const composerBySessionRef = useRef<Record<string, SessionComposerState>>({})

  const persistComposerState = useCallback(
    (sessionId: string) => {
      if (!sessionId) return
      if (inputAssetSessionId !== sessionId) {
        composerBySessionRef.current[sessionId] = {
          ...emptyComposerState(),
          inputText,
        }
        return
      }
      composerBySessionRef.current[sessionId] = {
        inputText,
        file,
        uploadedFileName,
        inputPreviewDataUrl,
        inputPreviewActive,
        serverReady,
        samplePreviewEpoch,
      }
    },
    [
      file,
      inputAssetSessionId,
      inputPreviewActive,
      inputPreviewDataUrl,
      inputText,
      samplePreviewEpoch,
      serverReady,
      uploadedFileName,
    ],
  )

  const restoreComposerState = useCallback((sessionId: string) => {
    const session = sessions.find((s) => s.id === sessionId)
    const snap = composerBySessionRef.current[sessionId] ?? emptyComposerState()
    const useSessionReferenceFallback =
      !snap.file &&
      !snap.uploadedFileName &&
      !snap.inputPreviewDataUrl &&
      !snap.inputPreviewActive &&
      !!session?.referenceImageName
    const resolvedUploadedName = useSessionReferenceFallback
      ? (session?.referenceImageName ?? null)
      : snap.uploadedFileName
    const resolvedPreviewActive = useSessionReferenceFallback
      ? true
      : snap.inputPreviewActive
    const resolvedServerReady = useSessionReferenceFallback
      ? true
      : snap.serverReady
    setInputText(snap.inputText)
    setFile(snap.file)
    setUploadedFileName(resolvedUploadedName)
    setInputPreviewDataUrl(snap.inputPreviewDataUrl)
    setInputPreviewActive(resolvedPreviewActive)
    setServerReady(resolvedServerReady)
    setSamplePreviewEpoch(snap.samplePreviewEpoch)
    if (
      snap.file ||
      resolvedUploadedName ||
      snap.inputPreviewDataUrl ||
      resolvedPreviewActive
    ) {
      setInputAssetSessionId(sessionId)
    } else {
      setInputAssetSessionId(null)
    }
  }, [sessions])

  const restoreComposerStateRef = useRef(restoreComposerState)
  restoreComposerStateRef.current = restoreComposerState

  useEffect(() => {
    if (!activeId) return
    // 僅在切換對話時還原輸入區。若依賴 restoreComposerState（隨 sessions 變化），上傳成功後
    // patchActiveSession 會觸發此 effect，此時 composer ref 可能尚未寫入，會誤清空預覽。
    restoreComposerStateRef.current(activeId)
    setThreadReferenceMissing(false)
  }, [activeId])

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
        // 載入後端的墓碑列表
        if (remote.deletedIds && remote.deletedIds.length) {
          deletedIdsRef.current = Array.from(
            new Set([...deletedIdsRef.current, ...remote.deletedIds]),
          )
        }
        setBoth((prev) => {
          const remoteSessions = (remote.sessions as ChatSession[]).filter(
            (s) => !deletedIdsRef.current.includes(s.id),
          )
          if (!remoteSessions.length) return prev
          sessionVersionRef.current = remote.version
          const tombstones = new Set(deletedIdsRef.current)
          const mergedSessions = mergeSessionLists(
            prev.sessions,
            remoteSessions,
            tombstones,
          )
          // 若使用者已開啟「尚未 commit」的暫存對話，不要用遠端 activeId 覆蓋
          const pend = pendingToolSessionRef.current
          if (
            pend &&
            prev.activeId === pend.id &&
            !remoteSessions.some((s) => s.id === prev.activeId)
          ) {
            return { sessions: mergedSessions, activeId: prev.activeId }
          }
          const activeOk = mergedSessions.some((s) => s.id === prev.activeId)
          const remoteActiveOk = mergedSessions.some((s) => s.id === remote.activeId)
          return {
            sessions: mergedSessions,
            activeId: activeOk
              ? prev.activeId
              : remoteActiveOk
                ? remote.activeId
                : mergedSessions[0]?.id ?? prev.activeId,
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

    // 快照：避免 closure 過舊
    const localSessions = sessions
    const localActiveId = activeId

    void (async () => {
      const localPending = pendingToolSessionRef.current
      // 最多重試 3 次，避免雙瀏覽器無限 409 循環
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const saved = await saveSessionState(baseUrl, {
            sessions: localSessions,
            activeId: localActiveId,
            version: sessionVersionRef.current,
            expectedVersion: sessionVersionRef.current,
            deletedIds: deletedIdsRef.current,
          })
          sessionVersionRef.current = saved.version
          // 同步後端回傳的 deletedIds（後端已 merge，更新本地墓碑）
          if (saved.deletedIds && saved.deletedIds.length) {
            deletedIdsRef.current = Array.from(
              new Set([...deletedIdsRef.current, ...saved.deletedIds]),
            )
          }
          return
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          if (msg !== "SESSION_STATE_CONFLICT") {
            // 後端暫時不可用，不阻斷操作
            return
          }
          // 409：GET 遠端最新，merge 後重試
          const remote = await fetchSessionState(baseUrl)
          if (!remote) return
          sessionVersionRef.current = remote.version

          // 合併墓碑：遠端已刪除的 ID，本地一律跟著刪
          if (remote.deletedIds && remote.deletedIds.length) {
            deletedIdsRef.current = Array.from(
              new Set([...deletedIdsRef.current, ...remote.deletedIds]),
            )
          }
          const tombstones = new Set(deletedIdsRef.current)

          const remoteSessions = (remote.sessions as ChatSession[]).filter(
            (s) => !tombstones.has(s.id),
          )
          const mergedSessions = mergeSessionLists(
            localSessions,
            remoteSessions,
            tombstones,
          )

          // 決定 activeId（暫存對話不在 mergedSessions 內，必須保留）
          const pendingIsActive =
            localPending &&
            localPending.id === localActiveId &&
            !mergedSessions.some((s) => s.id === localActiveId)
          const mergedActiveId = mergedSessions.some((s) => s.id === localActiveId)
            ? localActiveId
            : pendingIsActive
              ? localActiveId
              : mergedSessions.some((s) => s.id === remote.activeId)
                ? remote.activeId
                : mergedSessions[0]?.id ?? localActiveId

          setBoth({ sessions: mergedSessions, activeId: mergedActiveId })
          return
        }
      }
    })()
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
  const imageThreadLocked = activeSession?.imageThreadLocked ?? false

  const fallbackSamplePreviewUrl = useMemo(() => {
    if (inputAssetSessionId !== activeId) return null
    if (!serverReady) return null
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
    inputAssetSessionId,
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
    setInputAssetSessionId(null)
    setInputPreviewDataUrl(null)
    setInputPreviewActive(false)
    setServerReady(false)
  }, [])

  useEffect(() => {
    const handleStorage = (ev: StorageEvent) => {
      if (ev.key !== STORAGE_KEY) return
      const next = loadPersistedState()
      if (!next) return
      const normalized = normalizeIncomingState(next)
      flushMessagesScroll()
      setBoth({
        sessions: normalized.sessions,
        activeId: normalized.activeId,
      })
      setPendingToolSession(normalized.pendingToolSession)
      composerBySessionRef.current = {}
      resetInputAndUpload()
    }
    window.addEventListener("storage", handleStorage)
    return () => {
      window.removeEventListener("storage", handleStorage)
    }
  }, [flushMessagesScroll, resetInputAndUpload])

  const handleNewToolChat = useCallback(
    (toolId: string) => {
      persistComposerState(activeId)
      setMainView("chat")
      flushMessagesScroll()
      const s = createSession(toolId)
      // 暫存，不加入 sessions；等送出第一筆訊息時才 commit
      setPendingToolSession(s)
      setActiveIdOnly(s.id)
      restoreComposerState(s.id)
    },
    [
      activeId,
      flushMessagesScroll,
      persistComposerState,
      restoreComposerState,
      setActiveIdOnly,
    ],
  )

  const handleSelectChat = useCallback(
    (id: string) => {
      persistComposerState(activeId)
      setMainView("chat")
      flushMessagesScroll()
      setPendingToolSession(null)
      setActiveIdOnly(id)
      restoreComposerState(id)
    },
    [
      activeId,
      flushMessagesScroll,
      persistComposerState,
      restoreComposerState,
      setActiveIdOnly,
    ],
  )

  const handleDeleteChat = useCallback((id: string) => {
    flushMessagesScroll()
    const targetIds = sessions
      .filter((s) => s.id === id || s.parentId === id)
      .map((s) => s.id)
    // 寫入墓碑，確保其他瀏覽器在 merge 時也會移除這些 session
    deletedIdsRef.current = Array.from(
      new Set([...deletedIdsRef.current, ...targetIds]),
    )
    for (const sid of targetIds) {
      runControllersRef.current.get(sid)?.abort()
      runControllersRef.current.delete(sid)
      void deleteSessionUpload(sid, baseUrl).catch(() => {
        // 刪除對話時若後端檔案刪除失敗，不阻斷前端對話刪除流程
      })
      delete composerBySessionRef.current[sid]
    }
    let nextPending: ChatSession | null = null
    setBoth((prev) => {
      const deletedSession = prev.sessions.find((s) => s.id === id)
      const nextSessions = prev.sessions.filter(
        (s) => s.id !== id && s.parentId !== id,
      )
      if (nextSessions.length === 0) {
        const temp = createDefaultTemporaryToolSession()
        nextPending = temp
        return { sessions: [], activeId: temp.id }
      }
      let nextActive = prev.activeId
      if (prev.activeId === id || (deletedSession && prev.sessions.some((s) => s.parentId === id && s.id === prev.activeId))) {
        // 若被刪的是子 session 且它本身是 active：優先跳回父 session
        const parentId = deletedSession?.parentId
        const parentExists = parentId && nextSessions.some((s) => s.id === parentId)
        nextActive = parentExists ? parentId! : nextSessions[0].id
      }
      return { sessions: nextSessions, activeId: nextActive }
    })
    if (nextPending) {
      setPendingToolSession(nextPending)
    }
  }, [baseUrl, flushMessagesScroll, sessions])

  const handleOpenSettings = useCallback(() => {
    flushMessagesScroll()
    setMainView("settings")
  }, [flushMessagesScroll])

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
      if (taskCompleted || imageThreadLocked) {
        return
      }
      if (!next) {
        setFile(null)
        setUploadedFileName(null)
        setInputAssetSessionId(null)
        setInputPreviewDataUrl(null)
        setInputPreviewActive(false)
        setServerReady(false)
        composerBySessionRef.current[activeId] = {
          ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
          file: null,
          uploadedFileName: null,
          inputPreviewDataUrl: null,
          inputPreviewActive: false,
          serverReady: false,
          samplePreviewEpoch: 0,
        }
        patchActiveSession((s) => ({
          ...s,
          referenceImageName: undefined,
          updatedAt: Date.now(),
        }))
        void deleteSessionUploadImage(activeId, baseUrl).catch(() => {
          // 使用者手動移除預覽時，後端刪檔失敗不阻斷前端操作
        })
        return
      }

      const detached = await cloneFileDetached(next)
      setInputPreviewDataUrl(null)
      setInputPreviewActive(false)
      setFile(detached)
      setInputAssetSessionId(activeId)
      setUploading(true)
      try {
        await uploadImage(detached, baseUrl, activeId)
        setUploadedFileName(detached.name)
        patchActiveSession((s) => ({
          ...s,
          referenceImageName: detached.name,
          updatedAt: Date.now(),
        }))
        setServerReady(true)
        setInputPreviewActive(true)
        const nextEpoch = samplePreviewEpoch + 1
        setSamplePreviewEpoch(nextEpoch)
        try {
          const dataUrl = await readFileAsDataUrl(detached)
          setInputPreviewDataUrl(dataUrl)
          composerBySessionRef.current[activeId] = {
            ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
            file: detached,
            uploadedFileName: detached.name,
            inputPreviewDataUrl: dataUrl,
            inputPreviewActive: true,
            serverReady: true,
            samplePreviewEpoch: nextEpoch,
          }
        } catch {
          setInputPreviewDataUrl(null)
          composerBySessionRef.current[activeId] = {
            ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
            file: detached,
            uploadedFileName: detached.name,
            inputPreviewDataUrl: null,
            inputPreviewActive: true,
            serverReady: true,
            samplePreviewEpoch: nextEpoch,
          }
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        setFile(null)
        setUploadedFileName(null)
        setInputAssetSessionId(null)
        setInputPreviewDataUrl(null)
        setInputPreviewActive(false)
        setServerReady(false)
        composerBySessionRef.current[activeId] = {
          ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
          file: null,
          uploadedFileName: null,
          inputPreviewDataUrl: null,
          inputPreviewActive: false,
          serverReady: false,
          samplePreviewEpoch: 0,
        }
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
        patchActiveSession((s) => ({
          ...s,
          referenceImageName: undefined,
          updatedAt: Date.now(),
        }))
      } finally {
        setUploading(false)
      }
    },
    [
      activeId,
      baseUrl,
      imageThreadLocked,
      patchActiveMessages,
      samplePreviewEpoch,
      taskCompleted,
    ],
  )

  const handleSend = useCallback(async () => {
    if (taskCompleted) {
      return
    }
    const text = inputText.trim()

    // 子 session（圖片討論串）：走獨立的 image-thread API
    if (imageThreadLocked) {
      if (!text) return // 無文字不送出，由 UI 禁用按鈕保護

      const userMsg: ChatMessage = {
        id: newId(),
        role: "user",
        text,
      }
      patchActiveMessages((m) => [...m, userMsg])
      setInputText("")
      composerBySessionRef.current[activeId] = {
        ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
        inputText: "",
      }

      const sessionId = activeId
      patchSession(sessionId, (s) => ({
        ...s,
        isRunning: true,
        streamPrimed: true,
        updatedAt: Date.now(),
      }))
      const aborter = new AbortController()
      runControllersRef.current.set(sessionId, aborter)

      const sessionTitle = activeSession?.title ?? "thread"
      try {
        await consumeImageThreadStream(
          sessionId,
          text,
          sessionTitle,
          baseUrl,
          (ev) => {
            if (ev.type === "progress") {
              // 進度提示，不加入訊息列表
              return
            }
            if (ev.type === "complete") {
              const replyText = ev.text?.trim() || ""
              const base = baseUrl.replace(/\/+$/, "")
              const imageUrls = ev.saved_image
                ? [`${base}/images/${encodeURIComponent(ev.saved_image)}`]
                : []
              patchSessionMessages(sessionId, (m) => [
                ...m,
                {
                  id: newId(),
                  role: "assistant",
                  text: replyText || (imageUrls.length ? "已完成圖片修改。" : "已處理完畢。"),
                  textFormat: "plain" as const,
                  generatedImages: imageUrls.length ? imageUrls : undefined,
                },
              ])
              patchSession(sessionId, (s) => ({
                ...s,
                isRunning: false,
                streamPrimed: false,
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
              patchSession(sessionId, (s) => ({
                ...s,
                isRunning: false,
                streamPrimed: false,
                updatedAt: Date.now(),
              }))
            }
          },
          aborter.signal,
        )
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          patchSessionMessages(sessionId, (m) => [
            ...m,
            { id: newId(), role: "assistant", text: "已停止目前流程。" },
          ])
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
      return
    }

    // 一般 session（主 session）原有邏輯
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
    composerBySessionRef.current[activeId] = {
      ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
      inputText: "",
    }
    setFile(null)
    setUploadedFileName(null)
    setInputAssetSessionId(null)
    setInputPreviewDataUrl(null)
    setInputPreviewActive(false)
    setServerReady(false)
    composerBySessionRef.current[activeId] = {
      ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
      file: null,
      uploadedFileName: null,
      inputPreviewDataUrl: null,
      inputPreviewActive: false,
      serverReady: false,
      samplePreviewEpoch: 0,
    }
    patchActiveSession((s) => ({
      ...s,
      referenceImageName: undefined,
      updatedAt: Date.now(),
    }))

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
    activeSession?.title,
    baseUrl,
    file,
    inputPreviewDataUrl,
    serverReady,
    clearOnNextSend,
    imageThreadLocked,
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

  const handleOpenImageThread = useCallback(
    async (imageUrl: string, bubbleTitle: string, sourceKey: string) => {
      // 子 session 不允許再開啟下一層子 session
      if (activeSession?.parentId) return
      persistComposerState(activeId)
      const parentId = activeSession?.parentId ?? activeSession?.id
      if (!parentId) return

      // 若同一泡泡已有子 session，直接切過去
      const existingThread = sessions.find(
        (s) =>
          s.parentId === parentId &&
          s.threadSourceKey === sourceKey &&
          s.toolId === activeSession?.toolId,
      )
      if (existingThread) {
        setMainView("chat")
        flushSync(() => {
          flushMessagesScroll()
          setPendingToolSession(null)
          setActiveIdOnly(existingThread.id)
        })
        return
      }

      const threadSession: ChatSession = {
        ...createSession(activeSession?.toolId),
        parentId,
        title: normalizeBubbleTitle(bubbleTitle),
        imageThreadLocked: true,
        referenceImageName: undefined,
        threadSourceKey: sourceKey,
      }
      setMainView("chat")
      // 同步提交：避免 (1) 暫存主對話先被清掉導致 activeId 找不到 session
      // (2) flushMessagesScroll 的 setSessions 與切換對話拆成兩次 commit
      flushSync(() => {
        flushMessagesScroll()
        setBoth((prev) => {
          let nextSessions = prev.sessions
          const pend = pendingToolSessionRef.current
          if (
            pend &&
            pend.id === parentId &&
            !nextSessions.some((s) => s.id === parentId)
          ) {
            nextSessions = [pend, ...nextSessions]
          }
          return {
            sessions: insertChildSession(nextSessions, parentId, threadSession),
            activeId: threadSession.id,
          }
        })
        setPendingToolSession(null)
        setInputText("")
      })

      try {
        const filename = pictureFilenameFromImageUrl(imageUrl)
        if (!filename) {
          throw new Error("無法解析 picture 圖片檔名")
        }
        // 寫入初始圖片路徑到後端記憶系統
        await initImageThread(threadSession.id, filename, baseUrl)
        // 在聊天窗口以 assistant 泡泡顯示原始圖片
        const base = baseUrl.replace(/\/+$/, "")
        patchSessionMessages(threadSession.id, (m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            generatedImages: [`${base}/images/${encodeURIComponent(filename)}`],
          },
          {
            id: newId(),
            role: "assistant",
            text: "請描述你想對這張圖調整的內容（例如：背景、構圖、色調、光線）。",
          },
        ])
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        patchSessionMessages(threadSession.id, (m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            text: `建立圖片討論串失敗：${msg}`,
            error: true,
          },
        ])
      }
    },
    [
      activeSession?.toolId,
      activeSession?.id,
      activeSession?.parentId,
      activeId,
      baseUrl,
      flushMessagesScroll,
      patchSessionMessages,
      persistComposerState,
      sessions,
      setActiveIdOnly,
      setBoth,
    ],
  )

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
          onNewToolChat={handleNewToolChat}
          onSelect={handleSelectChat}
          onRename={handleRenameSession}
          onDelete={handleDeleteChat}
          settingsActive={mainView === "settings"}
          onOpenSettings={handleOpenSettings}
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
                  : activeSession?.parentId
                    ? activeSession.title
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
                  imageThreadLocked={imageThreadLocked}
                  onOpenImageThread={
                    activeSession?.parentId ? undefined : handleOpenImageThread
                  }
                />
                <InputBar
                  value={inputText}
                  onChange={(v) => {
                    setInputText(v)
                    composerBySessionRef.current[activeId] = {
                      ...(composerBySessionRef.current[activeId] ??
                        emptyComposerState()),
                      inputText: v,
                    }
                  }}
                  onSend={handleSend}
                  onStop={handleStop}
                  disabled={busy}
                  isStreaming={activeStreaming}
                  taskCompleted={taskCompleted}
                  file={imageThreadLocked ? null : (inputAssetSessionId === activeId ? file : null)}
                  uploadedFileName={
                    imageThreadLocked ? null : (inputAssetSessionId === activeId ? uploadedFileName : null)
                  }
                  previewUrl={imageThreadLocked ? null : (inputAssetSessionId === activeId ? fileObjectUrl : null)}
                  inputPreviewDataUrl={
                    imageThreadLocked ? null : (inputAssetSessionId === activeId ? inputPreviewDataUrl : null)
                  }
                  fallbackSamplePreviewUrl={
                    imageThreadLocked ? null : (inputAssetSessionId === activeId ? fallbackSamplePreviewUrl : null)
                  }
                  onFileChange={handleFileChange}
                  uploading={imageThreadLocked ? false : uploading}
                  lockImageThread={imageThreadLocked}
                  requireTextToSend={imageThreadLocked}
                />
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}
