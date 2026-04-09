import type { ChatMessage } from "./api"
import { DEFAULT_SESSION_TITLE } from "./types/chatSession"

const MAX_TITLE = 42

export function truncateTitle(text: string, max = MAX_TITLE): string {
  const t = text.replace(/\s+/g, " ").trim()
  if (t.length <= max) return t
  return `${t.slice(0, max - 1)}…`
}

/** 依第一則有意義的使用者訊息更新標題 */
export function titleFromMessages(
  messages: ChatMessage[],
  currentTitle: string,
): string {
  const isDefault = currentTitle === DEFAULT_SESSION_TITLE
  if (!isDefault) return currentTitle

  const first = messages.find(
    (m) =>
      m.role === "user" &&
      m.text &&
      m.text !== "（僅圖片）" &&
      m.text.trim().length > 0,
  )
  if (!first?.text) return currentTitle
  return truncateTitle(first.text)
}
