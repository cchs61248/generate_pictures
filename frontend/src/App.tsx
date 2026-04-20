import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { flushSync } from "react-dom"
import {
  type ChatMessage,
  cancelEcommerceRun,
  cancelImageThreadRun,
  consumeImageThreadStream,
  consumeImageThreadStreamSubscribe,
  consumeRunStream,
  consumeRunStreamSubscribe,
  deleteSessionDocument,
  deleteSessionDocuments,
  deleteSessionUpload,
  deleteSessionUploadImage,
  fetchEcommerceAwaitingPlan,
  fetchEcommerceRunStatus,
  fetchImageThreadRunStatus,
  fetchSessionState,
  getApiBaseUrl,
  imageUrlsFromSavedFiles,
  initImageThread,
  saveSessionState,
  fetchStyleLearningStatus,
  type ImageThreadStreamEvent,
  type SseEventMeta,
  type StreamEvent,
  type StyleProfile,
  uploadDocument,
  uploadImage,
} from "./api"
import {
  loadPersistedState,
  normalizePendingSession,
  prepareLocalStorageAfterFullPageReload,
  resolvePersistedMainView,
  savePersistedState,
  type PersistedState,
  type PersistedUiScroll,
} from "./chatStorage"
import { ChatWindow } from "./components/ChatWindow"
import { InputBar } from "./components/InputBar"
import { SettingsPage } from "./components/SettingsPage"
import { Sidebar } from "./components/Sidebar"
import { TokenUsagePage } from "./components/TokenUsagePage"
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

function createSession(toolId?: string, initialStyleProfileId = "none"): ChatSession {
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
    selectedStyleProfileId: initialStyleProfileId || "none",
    lastRunEventSeq: 0,
    imageGenerationMode: "auto",
    awaitingStage3Selection: false,
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
    if (!local) {
      merged.push(remoteSess)
      continue
    }
    const useLocalContent =
      (local.updatedAt ?? 0) >= (remoteSess.updatedAt ?? 0)
    const base = useLocalContent ? local : remoteSess
    const taskCompleted =
      Boolean(local.taskCompleted) || Boolean(remoteSess.taskCompleted)
    const isRunning = taskCompleted
      ? false
      : local.isRunning || remoteSess.isRunning
    const streamPrimed = taskCompleted
      ? false
      : local.streamPrimed || remoteSess.streamPrimed
    const awaitingStage3Selection = taskCompleted
      ? false
      : Boolean(
          local.awaitingStage3Selection || remoteSess.awaitingStage3Selection,
        )
    const imageGenerationMode =
      local.imageGenerationMode === "select" ||
      local.imageGenerationMode === "auto"
        ? local.imageGenerationMode
        : remoteSess.imageGenerationMode === "select" ||
            remoteSess.imageGenerationMode === "auto"
          ? remoteSess.imageGenerationMode
          : "auto"
    merged.push({
      ...base,
      title: titleFromMessages(base.messages, base.title),
      taskCompleted,
      isRunning,
      streamPrimed,
      awaitingStage3Selection,
      imageGenerationMode,
      // 訊息區捲動位置為本地 UI 狀態，與 updatedAt 無關；合併時優先保留任一端有紀錄者
      messagesScrollTop:
        local.messagesScrollTop ?? remoteSess.messagesScrollTop,
    })
  }
  for (const [, local] of localMap) {
    if (!remoteMap.has(local.id)) merged.push(local)
  }
  return merged
}

type DocEntry = { serverFilename: string; originalName: string }

type SessionComposerState = {
  inputText: string
  file: File | null
  uploadedFileName: string | null
  inputPreviewDataUrl: string | null
  inputPreviewActive: boolean
  serverReady: boolean
  samplePreviewEpoch: number
  docUploads: DocEntry[]
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
    docUploads: [],
  }
}

function createDefaultTemporaryToolSession(): ChatSession {
  const defaultToolId = TOOLS[0]?.id
  return createSession(defaultToolId)
}

function computeInitialState(persisted: PersistedState | null): {
  sessions: ChatSession[]
  activeId: string
  pendingToolSession: ChatSession | null
  mainView: "chat" | "settings" | "token_usage"
} {
  const mainView = resolvePersistedMainView(persisted)
  const pending = persisted
    ? normalizePendingSession(persisted.pendingToolSession)
    : null

  const pendingMatchesActive =
    !!pending &&
    pending.id === persisted?.activeId &&
    !pending.parentId

  if (persisted?.sessions?.length) {
    if (pendingMatchesActive) {
      return {
        sessions: persisted.sessions,
        activeId: persisted.activeId,
        pendingToolSession: pending,
        mainView,
      }
    }
    const activeOk = persisted.sessions.some((s) => s.id === persisted.activeId)
    return {
      sessions: persisted.sessions,
      activeId: activeOk ? persisted.activeId : persisted.sessions[0].id,
      pendingToolSession: null,
      mainView,
    }
  }

  if (pendingMatchesActive && persisted) {
    return {
      sessions: [],
      activeId: persisted.activeId,
      pendingToolSession: pending,
      mainView,
    }
  }

  const temp = createDefaultTemporaryToolSession()
  return { sessions: [], activeId: temp.id, pendingToolSession: temp, mainView }
}

/** 重新整理後已刪除 uploads 的 session：與後端合併時勿還原 referenceImageName */
function stripReferenceForPurgedSessions(
  sessions: ChatSession[],
  purgeIds: Set<string>,
): ChatSession[] {
  if (!purgeIds.size) return sessions
  return sessions.map((s) =>
    purgeIds.has(s.id) ? { ...s, referenceImageName: undefined } : s,
  )
}

const APP_BOOT = (() => {
  const prep = prepareLocalStorageAfterFullPageReload()
  const settingsTabInit: "env" | "style" =
    prep.persisted?.settingsTab === "style" ? "style" : "env"
  return {
    ...computeInitialState(prep.persisted),
    settingsTab: settingsTabInit,
    sessionIdsToDeleteUpload: prep.sessionIdsToDeleteUpload,
  }
})()

const REFERENCE_PURGE_ID_SET = new Set(APP_BOOT.sessionIdsToDeleteUpload)

function normalizeIncomingState(incoming: PersistedState): {
  sessions: ChatSession[]
  activeId: string
  pendingToolSession: ChatSession | null
} {
  const pending = normalizePendingSession(incoming.pendingToolSession)
  const sessions = incoming.sessions
  const activeId = incoming.activeId

  const pendingMatchesActive =
    !!pending &&
    pending.id === activeId &&
    !pending.parentId

  if (pendingMatchesActive) {
    if (sessions.length === 0) {
      return { sessions: [], activeId, pendingToolSession: pending }
    }
    return { sessions, activeId, pendingToolSession: pending }
  }

  if (sessions.length === 0) {
    const temp = createDefaultTemporaryToolSession()
    return { sessions: [], activeId: temp.id, pendingToolSession: temp }
  }
  const activeOk = sessions.some((s) => s.id === activeId)
  return {
    sessions,
    activeId: activeOk ? activeId : sessions[0].id,
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
  const baseUrl = getApiBaseUrl()
  const [{ sessions, activeId }, setBoth] = useState(() => ({
    sessions: APP_BOOT.sessions,
    activeId: APP_BOOT.activeId,
  }))
  const sessionsRef = useRef(sessions)
  sessionsRef.current = sessions
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
  const [docUploads, setDocUploads] = useState<DocEntry[]>([])
  const [uploadingDoc, setUploadingDoc] = useState(false)
  const [, setThreadReferenceMissing] = useState(false)
  const [hydratedFromServer, setHydratedFromServer] = useState(false)
  const sessionVersionRef = useRef(0)
  /** 防止舊的 session-state 同步流程覆蓋較新的前端狀態 */
  const saveStateGenRef = useRef(0)
  /** 已刪除的 session ID 墓碑（只累加），確保跨瀏覽器同步刪除 */
  const deletedIdsRef = useRef<string[]>([])
  const runControllersRef = useRef<Map<string, AbortController>>(new Map())
  /** StrictMode 會雙跑 effect；用世代號區分 abort 是否仍屬本輪，避免誤加「已停止」或清掉 isRunning */
  const resumeRunGenRef = useRef(0)
  const restorePlanGenRef = useRef(0)
  /** 由 ChatWindow 註冊：切換／新建對話前先寫入目前訊息區捲動位置 */
  const messagesScrollFlushRef = useRef<() => void>(() => {})
  const messagesScrollPersistTimerRef = useRef<ReturnType<
    typeof setTimeout
  > | null>(null)
  const settingsMainRef = useRef<HTMLElement | null>(null)
  const settingsMainScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  )
  const settingsMainScrollFlushRef = useRef<() => void>(() => {})
  const sidebarListScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  )
  const sidebarListScrollFlushRef = useRef<() => void>(() => {})
  const tokenTablesScrollFlushRef = useRef<() => void>(() => {})
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth > 900 : true,
  )
  const [mainView, setMainView] = useState<"chat" | "settings" | "token_usage">(
    () => APP_BOOT.mainView,
  )
  const [settingsTab, setSettingsTab] = useState<"env" | "style">(
    () => APP_BOOT.settingsTab,
  )
  const [styleExtractPending, setStyleExtractPendingState] = useState(() => {
    try {
      return loadPersistedState()?.styleExtractPending === true
    } catch {
      return false
    }
  })
  const setStyleExtractPending = useCallback((next: boolean) => {
    setStyleExtractPendingState(next)
  }, [])
  const styleExtractPollPrevPendingRef = useRef<number | null>(null)
  /** 與 styleDefaultProfileId 搭配：偵測 none→有效 id、或後端預設 uuid 換版，以同步尚未發話的電商主對話 */
  const prevEffectiveDefaultStyleProfileIdRef = useRef<string>("none")
  const prevStyleDefaultProfileIdFromServerRef = useRef<string | undefined>(
    undefined,
  )

  /* 舊版曾用 sessionStorage；遷移到與聊天狀態共用的 localStorage 後仍讀一次以免升級當下狀態遺失 */
  useEffect(() => {
    try {
      if (sessionStorage.getItem("gnerate_style_extract_pending") === "1") {
        setStyleExtractPendingState(true)
        sessionStorage.removeItem("gnerate_style_extract_pending")
      }
    } catch {
      /* ignore */
    }
  }, [])
  const [styleProfiles, setStyleProfiles] = useState<StyleProfile[]>([])
  const [styleDefaultProfileId, setStyleDefaultProfileId] = useState<string>("none")
  const styleProfileIdSet = useMemo(
    () => new Set(styleProfiles.map((p) => p.id)),
    [styleProfiles],
  )
  const effectiveDefaultStyleProfileId = useMemo(() => {
    if (styleDefaultProfileId === "none") return "none"
    return styleProfileIdSet.has(styleDefaultProfileId)
      ? styleDefaultProfileId
      : "none"
  }, [styleDefaultProfileId, styleProfileIdSet])
  /**
   * 點擊工具後建立的「暫存對話」，尚未加入 sessions。
   * 送出第一筆訊息時才正式 commit 進 sessions。
   */
  const [pendingToolSession, setPendingToolSession] = useState<ChatSession | null>(
    () => APP_BOOT.pendingToolSession,
  )
  const [uiScroll, setUiScroll] = useState<PersistedUiScroll>(
    () => loadPersistedState()?.uiScroll ?? {},
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
        docUploads,
      }
    },
    [
      docUploads,
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
    setDocUploads(snap.docUploads ?? [])
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
    const ids = APP_BOOT.sessionIdsToDeleteUpload
    if (!ids.length) return
    void (async () => {
      const seen = new Set<string>()
      for (const id of ids) {
        if (seen.has(id)) continue
        seen.add(id)
        try {
          const st = await fetchEcommerceRunStatus(id, baseUrl)
          if (st.status === "running") continue
        } catch {
          /* 後端不可用時仍嘗試清理本機預期 purge */
        }
        void deleteSessionUploadImage(id, baseUrl).catch(() => {})
        void deleteSessionDocuments(id, baseUrl).catch(() => {})
      }
    })()
  }, [baseUrl])

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
          const mergedSessions = stripReferenceForPurgedSessions(
            mergeSessionLists(
              prev.sessions,
              remoteSessions,
              tombstones,
            ),
            REFERENCE_PURGE_ID_SET,
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
    savePersistedState({
      sessions,
      activeId,
      mainView,
      pendingToolSession,
      uiScroll,
      styleExtractPending,
      settingsTab,
    })
    if (!hydratedFromServer) return

    // 快照：避免 closure 過舊
    const localSessions = sessions
    const localActiveId = activeId
    saveStateGenRef.current += 1
    const myGen = saveStateGenRef.current

    void (async () => {
      const localPending = pendingToolSessionRef.current
      // 最多重試 3 次，避免雙瀏覽器無限 409 循環
      for (let attempt = 0; attempt < 3; attempt++) {
        if (myGen !== saveStateGenRef.current) return
        try {
          const saved = await saveSessionState(baseUrl, {
            sessions: localSessions,
            activeId: localActiveId,
            version: sessionVersionRef.current,
            expectedVersion: sessionVersionRef.current,
            deletedIds: deletedIdsRef.current,
          })
          if (myGen !== saveStateGenRef.current) return
          sessionVersionRef.current = saved.version
          // 同步後端回傳的 deletedIds（後端已 merge，更新本地墓碑）
          if (saved.deletedIds && saved.deletedIds.length) {
            deletedIdsRef.current = Array.from(
              new Set([...deletedIdsRef.current, ...saved.deletedIds]),
            )
          }
          return
        } catch (e) {
          if (myGen !== saveStateGenRef.current) return
          const msg = e instanceof Error ? e.message : String(e)
          if (msg !== "SESSION_STATE_CONFLICT") {
            // 後端暫時不可用，不阻斷操作
            return
          }
          // 409：GET 遠端最新，merge 後重試
          const remote = await fetchSessionState(baseUrl)
          if (myGen !== saveStateGenRef.current) return
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
          const mergedSessions = stripReferenceForPurgedSessions(
            mergeSessionLists(
              localSessions,
              remoteSessions,
              tombstones,
            ),
            REFERENCE_PURGE_ID_SET,
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
  }, [
    activeId,
    baseUrl,
    hydratedFromServer,
    mainView,
    pendingToolSession,
    sessions,
    uiScroll,
    styleExtractPending,
    settingsTab,
  ])

  const activeSession = useMemo(() => {
    const committed = sessions.find((s) => s.id === activeId)
    if (committed) return committed
    if (pendingToolSession && pendingToolSession.id === activeId) {
      return pendingToolSession
    }
    return undefined
  }, [sessions, activeId, pendingToolSession])
  const sortedStyleProfiles = useMemo(() => {
    const defaultId = styleDefaultProfileId
    const getCreatedAt = (v?: string) => {
      const t = Date.parse(v ?? "")
      return Number.isNaN(t) ? 0 : t
    }
    return [...styleProfiles].sort((a, b) => {
      if (a.id === defaultId && b.id !== defaultId) return -1
      if (b.id === defaultId && a.id !== defaultId) return 1
      const av = a.version ?? -1
      const bv = b.version ?? -1
      if (av !== bv) return bv - av
      return getCreatedAt(b.created_at) - getCreatedAt(a.created_at)
    })
  }, [styleDefaultProfileId, styleProfiles])
  const resolveStyleProfileId = useCallback(
    (session: ChatSession | null | undefined): string => {
      const current = session?.selectedStyleProfileId
      if (current && current !== "none") {
        return styleProfileIdSet.has(current) ? current : effectiveDefaultStyleProfileId
      }
      if (current === "none") return "none"
      return effectiveDefaultStyleProfileId
    },
    [effectiveDefaultStyleProfileId, styleProfileIdSet],
  )
  const messages = activeSession?.messages ?? []
  const activeStreaming = activeSession?.isRunning ?? false
  const activeStreamPrimed = activeSession?.streamPrimed ?? false
  const taskCompleted = activeSession?.taskCompleted ?? false
  const awaitingSelection = activeSession?.awaitingStage3Selection ?? false
  /** taskCompleted 時不顯示串流 UI；待選圖時不顯示「輸入中」以免誤判為仍在跑 pipeline */
  const streamUiActive =
    activeStreaming && !taskCompleted && !awaitingSelection
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
      setPendingToolSession((p) =>
        p && p.id === sessionId ? { ...p, messagesScrollTop: scrollTop } : p,
      )
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

  const scheduleSettingsMainScrollPersist = useCallback(() => {
    if (settingsMainScrollTimerRef.current) {
      clearTimeout(settingsMainScrollTimerRef.current)
    }
    settingsMainScrollTimerRef.current = setTimeout(() => {
      settingsMainScrollTimerRef.current = null
      const el = settingsMainRef.current
      if (!el) return
      if (mainView === "settings") {
        setUiScroll((s) =>
          settingsTab === "style"
            ? { ...s, settingsStyleMain: el.scrollTop }
            : { ...s, settingsEnvMain: el.scrollTop },
        )
      } else if (mainView === "token_usage") {
        setUiScroll((s) => ({ ...s, tokenUsageMain: el.scrollTop }))
      }
    }, 400)
  }, [mainView, settingsTab])

  const flushSettingsMainScroll = useCallback(() => {
    if (settingsMainScrollTimerRef.current) {
      clearTimeout(settingsMainScrollTimerRef.current)
      settingsMainScrollTimerRef.current = null
    }
    settingsMainScrollFlushRef.current()
  }, [])

  useLayoutEffect(() => {
    settingsMainScrollFlushRef.current = () => {
      const el = settingsMainRef.current
      if (!el) return
      if (mainView === "settings") {
        setUiScroll((s) =>
          settingsTab === "style"
            ? { ...s, settingsStyleMain: el.scrollTop }
            : { ...s, settingsEnvMain: el.scrollTop },
        )
      } else if (mainView === "token_usage") {
        setUiScroll((s) => ({ ...s, tokenUsageMain: el.scrollTop }))
      }
    }
  }, [mainView, settingsTab])

  const scheduleSidebarListScrollPersist = useCallback((scrollTop: number) => {
    if (sidebarListScrollTimerRef.current) {
      clearTimeout(sidebarListScrollTimerRef.current)
    }
    sidebarListScrollTimerRef.current = setTimeout(() => {
      sidebarListScrollTimerRef.current = null
      setUiScroll((s) => ({ ...s, sidebarList: scrollTop }))
    }, 400)
  }, [])

  const flushSidebarListScroll = useCallback(() => {
    if (sidebarListScrollTimerRef.current) {
      clearTimeout(sidebarListScrollTimerRef.current)
      sidebarListScrollTimerRef.current = null
    }
    sidebarListScrollFlushRef.current()
  }, [])

  const persistSidebarListScrollNow = useCallback((scrollTop: number) => {
    setUiScroll((s) => ({ ...s, sidebarList: scrollTop }))
  }, [])

  const patchSidebarExpandedParents = useCallback(
    (next: Record<string, boolean>) => {
      setUiScroll((s) => ({ ...s, sidebarExpandedParents: next }))
    },
    [],
  )

  const persistTokenTableScrollX = useCallback(
    (which: "summary" | "detail", scrollLeft: number) => {
      setUiScroll((s) =>
        which === "summary"
          ? { ...s, tokenUsageSummaryTableX: scrollLeft }
          : { ...s, tokenUsageDetailTableX: scrollLeft },
      )
    },
    [],
  )

  const persistTokenUsageDateRange = useCallback(
    (next: { start: string; end: string }) => {
      setUiScroll((s) => ({
        ...s,
        tokenUsageStartDate: next.start,
        tokenUsageEndDate: next.end,
      }))
    },
    [],
  )

  /** 重新整理／關閉分頁前強制寫入捲動位置（避免 400ms debounce 尚未提交） */
  useEffect(() => {
    const onHidden = () => {
      flushMessagesScroll()
      flushSettingsMainScroll()
      flushSidebarListScroll()
      tokenTablesScrollFlushRef.current()
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") onHidden()
    }
    window.addEventListener("pagehide", onHidden)
    document.addEventListener("visibilitychange", onVisibilityChange)
    return () => {
      window.removeEventListener("pagehide", onHidden)
      document.removeEventListener("visibilitychange", onVisibilityChange)
    }
  }, [
    flushMessagesScroll,
    flushSettingsMainScroll,
    flushSidebarListScroll,
  ])

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
    setDocUploads([])
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const status = await fetchStyleLearningStatus(baseUrl)
        if (cancelled) return
        setStyleProfiles(status.profile?.profiles ?? [])
        setStyleDefaultProfileId(status.profile?.default_profile_id ?? "none")
      } catch {
        // 風格學習功能不可用時不阻斷主流程
      }
    })()
    return () => {
      cancelled = true
    }
  }, [baseUrl])

  const onSettingsStyleLearningChanged = useCallback(() => {
    void fetchStyleLearningStatus(baseUrl)
      .then((status) => {
        setStyleProfiles(status.profile?.profiles ?? [])
        setStyleDefaultProfileId(status.profile?.default_profile_id ?? "none")
      })
      .catch(() => {})
  }, [baseUrl])

  /* 重新整理後若仍持久化為萃取中（或請求被中斷），輪詢待萃取筆數；後端完成後筆數下降則解除 */
  useEffect(() => {
    if (!styleExtractPending) {
      styleExtractPollPrevPendingRef.current = null
      return
    }
    const tick = () => {
      void fetchStyleLearningStatus(baseUrl)
        .then((st) => {
          const q = st.queue_pending_total ?? 0
          const prev = styleExtractPollPrevPendingRef.current
          if (prev !== null && q < prev) {
            setStyleExtractPending(false)
            onSettingsStyleLearningChanged()
          }
          styleExtractPollPrevPendingRef.current = q
        })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 4000)
    return () => clearInterval(id)
  }, [
    styleExtractPending,
    baseUrl,
    setStyleExtractPending,
    onSettingsStyleLearningChanged,
  ])

  /* 避免異常狀態（例如強制關分頁）導致永遠顯示萃取中 */
  useEffect(() => {
    if (!styleExtractPending) return
    const maxWait = window.setTimeout(
      () => setStyleExtractPending(false),
      25 * 60 * 1000,
    )
    return () => window.clearTimeout(maxWait)
  }, [styleExtractPending, setStyleExtractPending])

  useEffect(() => {
    const prevEff = prevEffectiveDefaultStyleProfileIdRef.current
    const prevDef = prevStyleDefaultProfileIdFromServerRef.current

    if (effectiveDefaultStyleProfileId !== "none") {
      const shouldSyncPending = (sid: string | undefined) => {
        const cur = sid ?? "none"
        // 尚未載入風格列表前建立的暫存對話常為 "none"，載入後改為工具預設（已 commit 的 none 視為明確「不使用」）
        if (prevEff === "none" && cur === "none") return true
        if (
          prevDef !== undefined &&
          prevDef !== "none" &&
          cur === prevDef
        ) {
          return true
        }
        return false
      }
      const shouldSyncCommitted = (sid: string | undefined) => {
        const cur = sid ?? "none"
        if (
          prevDef !== undefined &&
          prevDef !== "none" &&
          cur === prevDef
        ) {
          return true
        }
        return false
      }

      setPendingToolSession((p) => {
        if (!p || p.toolId !== "ecommerce-image") return p
        if (p.messages.length > 0) return p
        if (!shouldSyncPending(p.selectedStyleProfileId)) return p
        return {
          ...p,
          selectedStyleProfileId: effectiveDefaultStyleProfileId,
          updatedAt: Date.now(),
        }
      })
      setSessions((prev) =>
        prev.map((s) => {
          if (s.toolId !== "ecommerce-image" || s.parentId) return s
          if (s.messages.length > 0) return s
          if (!shouldSyncCommitted(s.selectedStyleProfileId)) return s
          return {
            ...s,
            selectedStyleProfileId: effectiveDefaultStyleProfileId,
            updatedAt: Date.now(),
          }
        }),
      )
    }

    prevEffectiveDefaultStyleProfileIdRef.current =
      effectiveDefaultStyleProfileId
    prevStyleDefaultProfileIdFromServerRef.current = styleDefaultProfileId
  }, [
    effectiveDefaultStyleProfileId,
    styleDefaultProfileId,
    setSessions,
  ])

  useEffect(() => {
    const handleStorage = (ev: StorageEvent) => {
      if (ev.key !== STORAGE_KEY) return
      const next = loadPersistedState()
      if (!next) return
      const normalized = normalizeIncomingState(next)
      flushMessagesScroll()
      flushSettingsMainScroll()
      flushSidebarListScroll()
      tokenTablesScrollFlushRef.current()
      setBoth({
        sessions: normalized.sessions,
        activeId: normalized.activeId,
      })
      setPendingToolSession(normalized.pendingToolSession)
      setMainView(resolvePersistedMainView(next))
      setUiScroll(next.uiScroll ?? {})
      if (typeof next.styleExtractPending === "boolean") {
        setStyleExtractPendingState(next.styleExtractPending)
      }
      if (next.settingsTab === "env" || next.settingsTab === "style") {
        setSettingsTab(next.settingsTab)
      }
      composerBySessionRef.current = {}
      resetInputAndUpload()
    }
    window.addEventListener("storage", handleStorage)
    return () => {
      window.removeEventListener("storage", handleStorage)
    }
  }, [
    flushMessagesScroll,
    flushSettingsMainScroll,
    resetInputAndUpload,
    setStyleExtractPendingState,
    setSettingsTab,
  ])

  const handleNewToolChat = useCallback(
    (toolId: string) => {
      persistComposerState(activeId)
      setMainView("chat")
      flushMessagesScroll()
      const existingPending = pendingToolSessionRef.current
      if (existingPending && existingPending.toolId === toolId) {
        setActiveIdOnly(existingPending.id)
        restoreComposerState(existingPending.id)
        return
      }
      const s = createSession(toolId, effectiveDefaultStyleProfileId)
      // 暫存，不加入 sessions；等送出第一筆訊息時才 commit
      setPendingToolSession(s)
      setActiveIdOnly(s.id)
      restoreComposerState(s.id)
    },
    [
      activeId,
      effectiveDefaultStyleProfileId,
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
        const currentPending = pendingToolSessionRef.current
        const pendingAvailable =
          !!currentPending && !targetIds.includes(currentPending.id)
        if (pendingAvailable) {
          nextPending = currentPending
          return { sessions: [], activeId: currentPending.id }
        }
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

  const handleOpenTokenUsage = useCallback(() => {
    flushMessagesScroll()
    setMainView("token_usage")
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

      if (!next.type.startsWith("image/")) {
        window.alert("請上傳圖片檔案（JPG、PNG、WebP 等），不支援此檔案格式。")
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
        patchActiveSession((s) => ({
          ...s,
          referenceImageName: undefined,
          updatedAt: Date.now(),
        }))
        window.alert(msg || "請上傳有效的圖片檔案（JPG、PNG、WebP 等）。")
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

  const handleAddDoc = useCallback(
    async (docFile: File) => {
      if (taskCompleted || imageThreadLocked) return

      setUploadingDoc(true)
      try {
        let nextUploads = [...docUploads]
        // 若已有 3 個，刪除最舊的
        if (nextUploads.length >= 3) {
          const oldest = nextUploads[0]
          void deleteSessionDocument(activeId, oldest.serverFilename, baseUrl).catch(() => {})
          nextUploads = nextUploads.slice(1)
        }

        const result = await uploadDocument(docFile, baseUrl, activeId)
        const newEntry: DocEntry = {
          serverFilename: result.filename,
          originalName: docFile.name,
        }
        nextUploads = [...nextUploads, newEntry]
        setDocUploads(nextUploads)
        composerBySessionRef.current[activeId] = {
          ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
          docUploads: nextUploads,
        }
        patchActiveSession((s) => ({
          ...s,
          docFileNames: nextUploads,
          updatedAt: Date.now(),
        }))
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        window.alert(msg || "文件上傳失敗，請稍後再試。")
      } finally {
        setUploadingDoc(false)
      }
    },
    [activeId, baseUrl, docUploads, imageThreadLocked, patchActiveSession, taskCompleted],
  )

  const handleRemoveDoc = useCallback(
    (serverFilename: string) => {
      void deleteSessionDocument(activeId, serverFilename, baseUrl).catch(() => {})
      const nextUploads = docUploads.filter((d) => d.serverFilename !== serverFilename)
      setDocUploads(nextUploads)
      composerBySessionRef.current[activeId] = {
        ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
        docUploads: nextUploads,
      }
      patchActiveSession((s) => ({
        ...s,
        docFileNames: nextUploads,
        updatedAt: Date.now(),
      }))
    },
    [activeId, baseUrl, docUploads, patchActiveSession],
  )

  const applyImageThreadStreamEvent = useCallback(
    (sessionId: string, ev: ImageThreadStreamEvent, meta?: SseEventMeta) => {
      patchSession(sessionId, (s) => ({
        ...s,
        streamPrimed: true,
        lastRunEventSeq:
          meta?.eventId && meta.eventId > 0
            ? meta.eventId
            : (s.lastRunEventSeq ?? 0),
        updatedAt: Date.now(),
      }))
      if (ev.type === "progress") {
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
    [baseUrl, patchSession, patchSessionMessages],
  )

  const applyImageThreadStreamEventRef = useRef(applyImageThreadStreamEvent)
  applyImageThreadStreamEventRef.current = applyImageThreadStreamEvent

  const applyEcommerceRunStreamEvent = useCallback(
    (sessionId: string, ev: StreamEvent, meta?: SseEventMeta) => {
      patchSession(sessionId, (s) => ({
        ...s,
        streamPrimed: true,
        lastRunEventSeq:
          meta?.eventId && meta.eventId > 0
            ? meta.eventId
            : (s.lastRunEventSeq ?? 0),
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
        patchSessionMessages(sessionId, (m) => {
          let lastIdx = -1
          for (let i = 0; i < m.length; i++) {
            if (m[i].collapsible?.id === ev.group_id) lastIdx = i
          }
          if (lastIdx < 0) return m
          return m.map((msg, idx) => {
            if (idx !== lastIdx) return msg
            const c = msg.collapsible
            if (!c) return msg
            return {
              ...msg,
              collapsible: {
                ...c,
                lines: [...c.lines, ev.line],
              },
            }
          })
        })
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
      if (ev.type === "plan_ready") {
        patchSessionMessages(sessionId, (m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            planSelection: {
              items: ev.items,
              selectedSorts: [],
              settled: false,
            },
          },
        ])
        return
      }
      if (ev.type === "complete") {
        if (ev.awaiting_stage3_selection) {
          patchSession(sessionId, (s) => ({
            ...s,
            isRunning: false,
            streamPrimed: false,
            taskCompleted: false,
            awaitingStage3Selection: true,
            updatedAt: Date.now(),
          }))
          return
        }
        patchSessionMessages(sessionId, (m) =>
          m.map((msg) =>
            msg.planSelection && !msg.planSelection.settled
              ? {
                  ...msg,
                  planSelection: {
                    ...msg.planSelection,
                    settled: true,
                    cancelled: false,
                  },
                }
              : msg,
          ),
        )
        patchSession(sessionId, (s) => ({
          ...s,
          isRunning: false,
          streamPrimed: false,
          taskCompleted: true,
          awaitingStage3Selection: false,
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
        patchSession(sessionId, (s) => {
          const stillSelecting = s.messages.some(
            (msg) =>
              msg.planSelection &&
              !msg.planSelection.settled &&
              !msg.planSelection.cancelled,
          )
          return {
            ...s,
            isRunning: false,
            streamPrimed: false,
            updatedAt: Date.now(),
            awaitingStage3Selection: stillSelecting,
          }
        })
      }
    },
    [baseUrl, patchSession, patchSessionMessages],
  )

  const applyEcommerceRunStreamEventRef = useRef(applyEcommerceRunStreamEvent)
  applyEcommerceRunStreamEventRef.current = applyEcommerceRunStreamEvent

  const handlePlanToggleSort = useCallback(
    (sessionId: string, messageId: string, sort: number) => {
      patchSessionMessages(sessionId, (m) =>
        m.map((msg) => {
          if (
            msg.id !== messageId ||
            !msg.planSelection ||
            msg.planSelection.settled
          ) {
            return msg
          }
          const sel = msg.planSelection.selectedSorts
          const next = sel.includes(sort)
            ? sel.filter((x) => x !== sort)
            : [...sel, sort].sort((a, b) => a - b)
          return {
            ...msg,
            planSelection: { ...msg.planSelection, selectedSorts: next },
          }
        }),
      )
    },
    [patchSessionMessages],
  )

  const handlePlanCancel = useCallback(
    (sessionId: string, messageId: string) => {
      patchSessionMessages(sessionId, (m) =>
        m.map((msg) => {
          if (msg.id !== messageId || !msg.planSelection) return msg
          return {
            ...msg,
            planSelection: {
              ...msg.planSelection,
              settled: true,
              cancelled: true,
            },
          }
        }),
      )
      patchSession(sessionId, (s) => ({
        ...s,
        taskCompleted: true,
        awaitingStage3Selection: false,
        updatedAt: Date.now(),
      }))
    },
    [patchSession, patchSessionMessages],
  )

  const handlePlanConfirm = useCallback(
    async (sessionId: string, messageId: string) => {
      const sess =
        sessionsRef.current.find((s) => s.id === sessionId) ??
        pendingToolSessionRef.current
      const msg = sess?.messages.find((m) => m.id === messageId)
      const sorts = msg?.planSelection?.selectedSorts
      if (
        !sorts?.length ||
        !msg ||
        msg.planSelection?.settled ||
        runControllersRef.current.has(sessionId)
      ) {
        return
      }
      const selectedStyleProfileId = resolveStyleProfileId(sess ?? null)
      const aborter = new AbortController()
      runControllersRef.current.set(sessionId, aborter)
      patchSession(sessionId, (s) => ({
        ...s,
        isRunning: true,
        streamPrimed: false,
        awaitingStage3Selection: false,
        updatedAt: Date.now(),
      }))
      try {
        await consumeRunStream(
          "",
          baseUrl,
          (ev, meta) =>
            applyEcommerceRunStreamEventRef.current(sessionId, ev, meta),
          aborter.signal,
          sessionId,
          selectedStyleProfileId,
          {
            stage3Only: true,
            selectedSorts: sorts,
            imageGenerationMode: "auto",
          },
        )
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          patchSessionMessages(sessionId, (m) => [
            ...m,
            { id: newId(), role: "assistant", text: "已停止目前流程。" },
          ])
        } else {
          const errMsg = e instanceof Error ? e.message : String(e)
          patchSessionMessages(sessionId, (m) => [
            ...m,
            {
              id: newId(),
              role: "assistant",
              text: `執行失敗：${errMsg}`,
              error: true,
            },
          ])
        }
      } finally {
        if (runControllersRef.current.get(sessionId) === aborter) {
          runControllersRef.current.delete(sessionId)
        }
        patchSession(sessionId, (s) => {
          const stillSelecting = s.messages.some(
            (x) =>
              x.planSelection &&
              !x.planSelection.settled &&
              !x.planSelection.cancelled,
          )
          return {
            ...s,
            isRunning: false,
            streamPrimed: false,
            awaitingStage3Selection: stillSelecting,
            updatedAt: Date.now(),
          }
        })
      }
    },
    [baseUrl, patchSession, patchSessionMessages, resolveStyleProfileId],
  )

  const handleSend = useCallback(async () => {
    if (taskCompleted) {
      return
    }
    if (activeSession?.awaitingStage3Selection) {
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
      const selectedStyleProfileId = resolveStyleProfileId(activeSession)
      const aborter = new AbortController()
      runControllersRef.current.set(sessionId, aborter)
      patchSession(sessionId, (s) => ({
        ...s,
        isRunning: true,
        streamPrimed: false,
        updatedAt: Date.now(),
      }))

      const sessionTitle = activeSession?.title ?? "thread"
      try {
        await consumeImageThreadStream(
          sessionId,
          text,
          sessionTitle,
          selectedStyleProfileId,
          baseUrl,
          (ev, meta) => {
            applyImageThreadStreamEventRef.current(sessionId, ev, meta)
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

    // 一般 session（主 session）原有邏輯（送出鈕應已禁用；此為保險）
    if (!serverReady) {
      window.alert("請先使用左側圖片按鈕上傳一張商品圖片，成功後再送出。")
      return
    }

    // 若為暫存工具對話，送出時才正式加入 sessions
    if (pendingToolSession && pendingToolSession.id === activeId) {
      const pendingToCommit = pendingToolSession
      flushSync(() => {
        setSessions((prev) => {
          if (prev.some((s) => s.id === pendingToCommit.id)) return prev
          return [pendingToCommit, ...prev]
        })
        setPendingToolSession(null)
      })
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

    const docSnapshot = docUploads.map((d) => ({
      originalName: d.originalName,
      serverFilename: d.serverFilename,
    }))

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
      text:
        text ||
        (docSnapshot.length > 0
          ? imageSnapshot
            ? "（已送出商品圖與附件）"
            : "（已送出附件）"
          : "（僅圖片）"),
      imagePreview: imageSnapshot,
      attachedDocuments:
        docSnapshot.length > 0 ? docSnapshot : undefined,
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
    setDocUploads([])
    composerBySessionRef.current[activeId] = {
      ...(composerBySessionRef.current[activeId] ?? emptyComposerState()),
      file: null,
      uploadedFileName: null,
      inputPreviewDataUrl: null,
      inputPreviewActive: false,
      serverReady: false,
      samplePreviewEpoch: 0,
      docUploads: [],
    }
    patchActiveSession((s) => ({
      ...s,
      referenceImageName: undefined,
      docFileNames: [],
      updatedAt: Date.now(),
    }))

    const sessionId = activeId
    const aborter = new AbortController()
    runControllersRef.current.set(sessionId, aborter)
    patchSession(sessionId, (s) => ({
      ...s,
      isRunning: true,
      streamPrimed: false,
      lastRunEventSeq: 0,
      updatedAt: Date.now(),
    }))

    try {
      await consumeRunStream(
        text,
        baseUrl,
        (ev, meta) => applyEcommerceRunStreamEvent(sessionId, ev, meta),
        aborter.signal,
        sessionId,
        resolveStyleProfileId(activeSession),
        {
          imageGenerationMode: activeSession?.imageGenerationMode ?? "auto",
        },
      )
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
    activeSession?.awaitingStage3Selection,
    activeSession?.imageGenerationMode,
    activeSession?.title,
    activeSession?.selectedStyleProfileId,
    baseUrl,
    docUploads,
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
    applyEcommerceRunStreamEvent,
  ])

  useEffect(() => {
    if (!hydratedFromServer) return
    const sid = activeId
    const pend = pendingToolSessionRef.current
    const sess =
      pend && pend.id === sid && !pend.parentId
        ? pend
        : sessionsRef.current.find((s) => s.id === sid)
    if (!sess || sess.toolId !== "ecommerce-image") return
    if (sess.taskCompleted) return
    if (runControllersRef.current.has(sid)) return

    resumeRunGenRef.current += 1
    const myGen = resumeRunGenRef.current

    const aborter = new AbortController()
    void (async () => {
      let subscribed = false
      try {
        const isImageThread = Boolean(sess.parentId || sess.imageThreadLocked)
        let st: Awaited<ReturnType<typeof fetchEcommerceRunStatus>> | null = null
        let stImg: Awaited<ReturnType<typeof fetchImageThreadRunStatus>> | null = null
        try {
          if (isImageThread) {
            stImg = await fetchImageThreadRunStatus(sid, baseUrl, aborter.signal)
          } else {
            st = await fetchEcommerceRunStatus(sid, baseUrl, aborter.signal)
          }
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return
          return
        }
        if (myGen !== resumeRunGenRef.current) return
        if (aborter.signal.aborted) return
        const running = isImageThread
          ? Boolean(stImg && stImg.status === "running")
          : Boolean(st && st.status === "running")
        if (!running) {
          patchSession(sid, (s) =>
            s.isRunning || s.streamPrimed
              ? {
                  ...s,
                  isRunning: false,
                  streamPrimed: false,
                  updatedAt: Date.now(),
                }
              : s,
          )
          return
        }
        if (runControllersRef.current.has(sid)) return

        const resumeFromSeq = Math.max(0, Math.trunc(sess.lastRunEventSeq ?? 0))
        patchSession(sid, (s) => ({
          ...s,
          isRunning: true,
          streamPrimed: false,
          updatedAt: Date.now(),
        }))
        if (myGen !== resumeRunGenRef.current) return
        runControllersRef.current.set(sid, aborter)
        subscribed = true
        if (isImageThread) {
          await consumeImageThreadStreamSubscribe(
            sid,
            baseUrl,
            (ev, meta) => {
              applyImageThreadStreamEventRef.current(sid, ev, meta)
            },
            aborter.signal,
            resumeFromSeq,
          )
        } else {
          await consumeRunStreamSubscribe(
            sid,
            baseUrl,
            (ev, meta) => {
              applyEcommerceRunStreamEventRef.current(sid, ev, meta)
            },
            aborter.signal,
            resumeFromSeq,
          )
        }
      } catch (e) {
        if (myGen !== resumeRunGenRef.current) return
        if (e instanceof DOMException && e.name === "AbortError") {
          if (!subscribed) return
          patchSessionMessages(sid, (m) => [
            ...m,
            { id: newId(), role: "assistant", text: "已停止目前流程。" },
          ])
          patchSession(sid, (s) => ({
            ...s,
            isRunning: false,
            streamPrimed: false,
            clearOnNextSend: true,
            updatedAt: Date.now(),
          }))
          return
        }
        const msg = e instanceof Error ? e.message : String(e)
        patchSessionMessages(sid, (m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            text: `執行失敗：${msg}`,
            error: true,
          },
        ])
      } finally {
        if (subscribed) {
          if (runControllersRef.current.get(sid) === aborter) {
            runControllersRef.current.delete(sid)
          }
          if (myGen === resumeRunGenRef.current) {
            patchSession(sid, (s) => ({
              ...s,
              isRunning: false,
              streamPrimed: false,
              updatedAt: Date.now(),
            }))
          }
        }
      }
    })()
    return () => {
      resumeRunGenRef.current += 1
      aborter.abort()
    }
  }, [hydratedFromServer, activeId, baseUrl, patchSession, patchSessionMessages])

  /** 待選圖：localStorage 無卡片時自後端 run_job 還原 */
  useEffect(() => {
    if (!hydratedFromServer) return
    const sid = activeId
    const pend = pendingToolSessionRef.current
    const sess =
      pend && pend.id === sid && !pend.parentId
        ? pend
        : sessionsRef.current.find((s) => s.id === sid)
    if (!sess || sess.toolId !== "ecommerce-image") return
    if (sess.parentId) return
    if (sess.taskCompleted) return
    if (runControllersRef.current.has(sid)) return

    const hasUnsettled = sess.messages.some(
      (m) => m.planSelection && !m.planSelection.settled,
    )
    if (hasUnsettled) {
      // 若已進入階段三執行中（例如選圖後產圖途中刷新），不可回退成「待選圖」，
      // 否則會把 loading/停止按鈕隱藏掉。
      if (sess.isRunning) {
        return
      }
      if (!sess.awaitingStage3Selection) {
        patchSession(sid, (s) => ({
          ...s,
          awaitingStage3Selection: true,
          updatedAt: Date.now(),
        }))
      }
      return
    }

    restorePlanGenRef.current += 1
    const myGen = restorePlanGenRef.current
    const aborter = new AbortController()
    void (async () => {
      try {
        const st = await fetchEcommerceRunStatus(sid, baseUrl, aborter.signal)
        if (myGen !== restorePlanGenRef.current) return
        if (!st.awaiting_stage3_selection) return
        const plan = await fetchEcommerceAwaitingPlan(
          sid,
          baseUrl,
          aborter.signal,
        )
        if (myGen !== restorePlanGenRef.current) return
        const planItems = plan.items
        if (!plan.awaiting || !planItems?.length) return
        patchSessionMessages(sid, (m) => [
          ...m,
          {
            id: newId(),
            role: "assistant",
            planSelection: {
              items: planItems,
              selectedSorts: [],
              settled: false,
            },
          },
        ])
        patchSession(sid, (s) => ({
          ...s,
          awaitingStage3Selection: true,
          taskCompleted: false,
          updatedAt: Date.now(),
        }))
      } catch {
        /* 無紀錄或非待選狀態 */
      }
    })()
    return () => {
      restorePlanGenRef.current += 1
      aborter.abort()
    }
  }, [hydratedFromServer, activeId, baseUrl, patchSession, patchSessionMessages])

  /** 待選圖時鎖定輸入列（文字、送出、上傳圖／附件）；選圖卡片另由 planInteractionLocked 控制 */
  const busy = streamUiActive || uploading || awaitingSelection
  const handleStop = useCallback(() => {
    const sid = activeId
    runControllersRef.current.get(sid)?.abort()
    const isImageThread = Boolean(
      activeSession?.parentId || activeSession?.imageThreadLocked,
    )
    if (isImageThread) {
      void cancelImageThreadRun(sid, baseUrl).catch(() => {})
      if (!runControllersRef.current.has(sid)) {
        patchSession(sid, (s) =>
          s.isRunning || s.streamPrimed
            ? {
                ...s,
                isRunning: false,
                streamPrimed: false,
                updatedAt: Date.now(),
              }
            : s,
        )
      }
      return
    }
    void cancelEcommerceRun(sid, baseUrl).catch(() => {})
    if (!runControllersRef.current.has(sid)) {
      patchSession(sid, (s) =>
        s.isRunning || s.streamPrimed
          ? {
              ...s,
              isRunning: false,
              streamPrimed: false,
              updatedAt: Date.now(),
            }
          : s,
      )
    }
  }, [
    activeId,
    activeSession?.imageThreadLocked,
    activeSession?.parentId,
    baseUrl,
    patchSession,
  ])

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
        selectedStyleProfileId: resolveStyleProfileId(activeSession),
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
          tokenUsageActive={mainView === "token_usage"}
          onOpenTokenUsage={handleOpenTokenUsage}
          sessionHighlightActive={mainView === "chat"}
          listScrollTop={uiScroll.sidebarList}
          onListScrollPersist={scheduleSidebarListScrollPersist}
          onListScrollPersistNow={persistSidebarListScrollNow}
          listScrollFlushRef={sidebarListScrollFlushRef}
          expandedParents={uiScroll.sidebarExpandedParents ?? {}}
          onExpandedParentsChange={patchSidebarExpandedParents}
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
            {mainView === "settings" || mainView === "token_usage" ? (
              <button
                type="button"
                className="app-header-back"
                onClick={() => setMainView("chat")}
              >
                ← 返回聊天
              </button>
            ) : null}
            <div className="app-header-titles">
              <div className="app-title-row">
                <h1 className="app-title">
                  {mainView === "settings"
                    ? "設定與說明"
                    : mainView === "token_usage"
                      ? "Token 用量"
                      : activeSession?.parentId
                      ? activeSession.title
                      : activeSession?.toolId
                        ? (getToolById(activeSession.toolId)?.chatTitle ?? "AI 助手")
                        : "AI 電商圖文助手"}
                </h1>
              </div>
              <p className="app-sub">
                <span
                  className="app-api-status"
                  aria-hidden
                  title="後端位址（開發時預設本機）"
                />
                <span>API：{baseUrl}</span>
              </p>
            </div>
          </header>
          <main
            ref={settingsMainRef}
            onScroll={
              mainView === "settings" || mainView === "token_usage"
                ? scheduleSettingsMainScrollPersist
                : undefined
            }
            className={
              mainView === "settings" || mainView === "token_usage"
                ? "app-main app-main--settings"
                : "app-main app-main--chat"
            }
          >
            {mainView === "settings" ? (
              <SettingsPage
                baseUrl={baseUrl}
                scrollContainerRef={settingsMainRef}
                savedMainScrollTop={
                  settingsTab === "style"
                    ? (uiScroll.settingsStyleMain ?? uiScroll.settingsMain)
                    : (uiScroll.settingsEnvMain ?? uiScroll.settingsMain)
                }
                activeTab={settingsTab}
                onTabChange={setSettingsTab}
                onStyleLearningChanged={onSettingsStyleLearningChanged}
                styleExtractPending={styleExtractPending}
                setStyleExtractPending={setStyleExtractPending}
              />
            ) : mainView === "token_usage" ? (
              <TokenUsagePage
                baseUrl={baseUrl}
                scrollContainerRef={settingsMainRef}
                savedMainScrollTop={uiScroll.tokenUsageMain}
                savedTableScrollX={{
                  summary: uiScroll.tokenUsageSummaryTableX,
                  detail: uiScroll.tokenUsageDetailTableX,
                }}
                onTableScrollXPersist={persistTokenTableScrollX}
                tableScrollFlushRef={tokenTablesScrollFlushRef}
                savedDateRange={{
                  start: uiScroll.tokenUsageStartDate,
                  end: uiScroll.tokenUsageEndDate,
                }}
                onDateRangeChange={persistTokenUsageDateRange}
              />
            ) : (
              <>
                <ChatWindow
                  sessionId={activeId}
                  savedScrollTop={activeSession?.messagesScrollTop}
                  scheduleScrollTopPersist={scheduleMessagesScrollPersist}
                  persistScrollTopNow={persistMessagesScroll}
                  scrollFlushRef={messagesScrollFlushRef}
                  messages={messages}
                  streaming={streamUiActive}
                  streamPrimed={streamUiActive && activeStreamPrimed}
                  toolId={activeSession?.toolId}
                  imageThreadLocked={imageThreadLocked}
                  onOpenImageThread={
                    activeSession?.parentId ? undefined : handleOpenImageThread
                  }
                  imageGenerationMode={
                    activeSession?.imageGenerationMode ?? "auto"
                  }
                  onImageGenerationModeChange={(mode) => {
                    patchActiveSession((s) => ({
                      ...s,
                      imageGenerationMode: mode,
                      updatedAt: Date.now(),
                    }))
                  }}
                  imageGenModeLocked={
                    messages.length > 0 ||
                    streamUiActive ||
                    taskCompleted ||
                    awaitingSelection
                  }
                  planInteractionLocked={Boolean(
                    activeSession?.isRunning && !awaitingSelection,
                  )}
                  onPlanToggleSort={(messageId, sort) =>
                    handlePlanToggleSort(activeId, messageId, sort)
                  }
                  onPlanConfirm={(messageId) =>
                    void handlePlanConfirm(activeId, messageId)
                  }
                  onPlanCancel={(messageId) =>
                    handlePlanCancel(activeId, messageId)
                  }
                  styleProfileValue={resolveStyleProfileId(activeSession)}
                  styleProfileOptions={sortedStyleProfiles.map((p) => ({
                    id: p.id,
                    name: p.name,
                  }))}
                  onStyleProfileChange={(profileId) => {
                    patchActiveSession((s) => ({
                      ...s,
                      selectedStyleProfileId: profileId || "none",
                      updatedAt: Date.now(),
                    }))
                  }}
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
                  isStreaming={streamUiActive}
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
                  requireReferenceToSend={!imageThreadLocked}
                  referenceReady={serverReady}
                  docUploads={imageThreadLocked ? [] : docUploads}
                  onAddDoc={imageThreadLocked ? undefined : handleAddDoc}
                  onRemoveDoc={imageThreadLocked ? undefined : handleRemoveDoc}
                  uploadingDoc={uploadingDoc}
                />
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}
