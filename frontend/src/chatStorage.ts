import type { ChatSession } from "./types/chatSession"

const STORAGE_KEY = "gnerate_pictures_chat_v1"

/** 與 App.tsx mainView 對齊；持久化以便重新整理後留在同一畫面 */
export type PersistedMainView = "chat" | "settings" | "token_usage"

/** 側欄／設定與 Token 頁等 UI 捲動位置（與對話 messagesScrollTop 分開存） */
export type PersistedUiScroll = {
  sidebarList?: number
  /** 側欄主 session（有子討論串）是否展開；缺省為 true，顯式 false 表示收合 */
  sidebarExpandedParents?: Record<string, boolean>
  /** 設定頁：環境變數 tab 的垂直捲動位置 */
  settingsEnvMain?: number
  /** 設定頁：風格學習 tab 的垂直捲動位置 */
  settingsStyleMain?: number
  /** 舊版相容（單一設定頁捲動位置） */
  settingsMain?: number
  tokenUsageMain?: number
  tokenUsageSummaryTableX?: number
  tokenUsageDetailTableX?: number
  tokenUsageStartDate?: string
  tokenUsageEndDate?: string
}

export type PersistedState = {
  sessions: ChatSession[]
  activeId: string
  mainView?: PersistedMainView
  /**
   * 點選工具後、尚未送出第一則訊息前的暫存對話（不在 sessions 陣列內）
   * 與 activeId 成對還原，避免重新整理後被誤判成「第一個 session」
   */
  pendingToolSession?: ChatSession | null
  uiScroll?: PersistedUiScroll
  /** 設定頁手動風格萃取進行中（與後端非同步；需與主狀態一併持久化） */
  styleExtractPending?: boolean
}

/**
 * 驗證並正規化持久化的暫存對話；無效則回傳 null
 */
export function normalizePendingSession(raw: unknown): ChatSession | null {
  if (raw == null || typeof raw !== "object") return null
  const p = raw as Partial<ChatSession>
  if (typeof p.id !== "string" || !p.id.trim()) return null
  if (p.parentId) return null
  if (typeof p.title !== "string") return null
  if (typeof p.toolId !== "string" || !p.toolId.trim()) return null
  return {
    id: p.id,
    title: p.title,
    messages: Array.isArray(p.messages) ? p.messages : [],
    messagesScrollTop: p.messagesScrollTop,
    updatedAt: typeof p.updatedAt === "number" ? p.updatedAt : Date.now(),
    isRunning: false,
    streamPrimed: false,
    taskCompleted: Boolean(p.taskCompleted),
    clearOnNextSend: Boolean(p.clearOnNextSend),
    toolId: p.toolId,
    imageThreadLocked: p.imageThreadLocked,
    referenceImageName: p.referenceImageName,
    docFileNames: Array.isArray(p.docFileNames) ? p.docFileNames : undefined,
    threadSourceKey: p.threadSourceKey,
    selectedStyleProfileId:
      typeof p.selectedStyleProfileId === "string"
        ? p.selectedStyleProfileId
        : "none",
    lastRunEventSeq:
      typeof p.lastRunEventSeq === "number" && Number.isFinite(p.lastRunEventSeq)
        ? Math.max(0, Math.trunc(p.lastRunEventSeq))
        : 0,
    imageGenerationMode:
      p.imageGenerationMode === "select" || p.imageGenerationMode === "auto"
        ? p.imageGenerationMode
        : "auto",
    awaitingStage3Selection: Boolean(p.awaitingStage3Selection),
  }
}

/** 從持久化資料解析合法 mainView（無效則為 chat） */
export function resolvePersistedMainView(
  state: PersistedState | null,
): PersistedMainView {
  const v = state?.mainView
  if (v === "settings" || v === "token_usage") return v
  return "chat"
}

/**
 * 完整重新載入時：
 * 只有當前停留在聊天頁，且 active 對話為「AI 電商圖文助手」時，
 * 才清除該 active session 的上傳狀態與後端 uploads 資源。
 */
export function prepareLocalStorageAfterFullPageReload(): {
  persisted: PersistedState | null
  sessionIdsToDeleteUpload: string[]
  sessionIdsToDeleteDocs: string[]
} {
  const persisted = loadPersistedState()
  if (!persisted) {
    return { persisted: null, sessionIdsToDeleteUpload: [], sessionIdsToDeleteDocs: [] }
  }

  const activePending =
    persisted.pendingToolSession?.id === persisted.activeId
      ? persisted.pendingToolSession
      : null
  const shouldPurgeActiveOnly =
    persisted.mainView === "chat" &&
    activePending?.toolId === "ecommerce-image" &&
    Boolean(persisted.activeId)
  const purgeIds =
    shouldPurgeActiveOnly && persisted.activeId ? [persisted.activeId] : []

  const sessions = persisted.sessions.map((s) => ({
    ...s,
    referenceImageName:
      shouldPurgeActiveOnly && s.id === persisted.activeId
        ? undefined
        : s.referenceImageName,
    docFileNames:
      shouldPurgeActiveOnly && s.id === persisted.activeId
        ? []
        : s.docFileNames,
  }))
  let nextPending = persisted.pendingToolSession
  if (
    shouldPurgeActiveOnly &&
    nextPending &&
    nextPending.id === persisted.activeId
  ) {
    nextPending = {
      ...nextPending,
      referenceImageName: undefined,
      docFileNames: [],
    }
  }
  const next: PersistedState = {
    ...persisted,
    sessions,
    pendingToolSession: nextPending,
  }
  savePersistedState(next)
  return {
    persisted: next,
    sessionIdsToDeleteUpload: purgeIds,
    sessionIdsToDeleteDocs: purgeIds,
  }
}

export function loadPersistedState(): PersistedState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as PersistedState
    if (!Array.isArray(data.sessions) || typeof data.activeId !== "string") {
      return null
    }
    const activeId = data.activeId
    // backward compat: older builds lacked these fields
    data.sessions = data.sessions.map((s) => ({
      ...s,
      // full reload: keep isRunning for active session only (ecommerce / image-thread both use /status + subscribe).
      isRunning:
        s.id === activeId
          ? Boolean((s as Partial<ChatSession>).isRunning)
          : false,
      streamPrimed: false,
      taskCompleted: Boolean((s as Partial<ChatSession>).taskCompleted),
      clearOnNextSend: Boolean((s as Partial<ChatSession>).clearOnNextSend),
      selectedStyleProfileId:
        typeof (s as Partial<ChatSession>).selectedStyleProfileId === "string"
          ? (s as Partial<ChatSession>).selectedStyleProfileId
          : "none",
      lastRunEventSeq:
        typeof (s as Partial<ChatSession>).lastRunEventSeq === "number" &&
        Number.isFinite((s as Partial<ChatSession>).lastRunEventSeq)
          ? Math.max(0, Math.trunc((s as Partial<ChatSession>).lastRunEventSeq as number))
          : 0,
      imageGenerationMode:
        (s as Partial<ChatSession>).imageGenerationMode === "select" ||
        (s as Partial<ChatSession>).imageGenerationMode === "auto"
          ? (s as Partial<ChatSession>).imageGenerationMode
          : "auto",
      awaitingStage3Selection: Boolean(
        (s as Partial<ChatSession>).awaitingStage3Selection,
      ),
    }))
    const p = normalizePendingSession(data.pendingToolSession)
    if (p) {
      data.pendingToolSession = p
    } else {
      delete data.pendingToolSession
    }
    return data
  } catch {
    return null
  }
}

export function savePersistedState(state: PersistedState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    /* 配額或其他錯誤時略過 */
  }
}
