import type { ChatMessage } from "../api"

export type ChatSession = {
  id: string
  /** 列表顯示用，通常取自第一則使用者文字 */
  title: string
  messages: ChatMessage[]
  /** 對話訊息區上次捲動位置（依 session 還原） */
  messagesScrollTop?: number
  updatedAt: number
  /** 此對話是否正在執行流程 */
  isRunning: boolean
  /** 串流是否已收到第一筆事件 */
  streamPrimed: boolean
  /** 此對話任務是否已完成（完成後鎖住輸入） */
  taskCompleted: boolean
  /** 若上次是手動停止，下一次送出前先清空舊訊息 */
  clearOnNextSend: boolean
  /** 此對話所屬工具 ID（undefined 表示一般對話） */
  toolId?: string
}

export const DEFAULT_SESSION_TITLE = "新對話"
