import type { ChatMessage } from "../api"

export type ChatSession = {
  id: string
  /** 列表顯示用，通常取自第一則使用者文字 */
  title: string
  messages: ChatMessage[]
  updatedAt: number
}

export const DEFAULT_SESSION_TITLE = "新對話"
