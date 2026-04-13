import type { ChatSession } from "./types/chatSession"

const STORAGE_KEY = "gnerate_pictures_chat_v1"

/** 與 App.tsx mainView 對齊；持久化以便重新整理後留在同一畫面 */
export type PersistedMainView = "chat" | "settings" | "token_usage"

/** 側欄／設定與 Token 頁等 UI 捲動位置（與對話 messagesScrollTop 分開存） */
export type PersistedUiScroll = {
  sidebarList?: number
  settingsMain?: number
  tokenUsageMain?: number
  tokenUsageSummaryTableX?: number
  tokenUsageDetailTableX?: number
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
    threadSourceKey: p.threadSourceKey,
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

export function loadPersistedState(): PersistedState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as PersistedState
    if (!Array.isArray(data.sessions) || typeof data.activeId !== "string") {
      return null
    }
    // 向後相容：舊版沒有狀態欄位
    data.sessions = data.sessions.map((s) => ({
      ...s,
      // 重新整理後不延續「執行中」狀態
      isRunning: false,
      streamPrimed: false,
      taskCompleted: Boolean((s as Partial<ChatSession>).taskCompleted),
      clearOnNextSend: Boolean((s as Partial<ChatSession>).clearOnNextSend),
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
