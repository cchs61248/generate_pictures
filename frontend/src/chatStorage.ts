import type { ChatSession } from "./types/chatSession"

const STORAGE_KEY = "gnerate_pictures_chat_v1"

export type PersistedState = {
  sessions: ChatSession[]
  activeId: string
}

export function loadPersistedState(): PersistedState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as PersistedState
    if (!Array.isArray(data.sessions) || typeof data.activeId !== "string") {
      return null
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
