import type { ChatMessage } from "../api"

export type ChatSession = {
  id: string
  /** 子討論串所屬主 session（主 session 為 undefined） */
  parentId?: string
  /** 列表顯示用，通常取自第一則使用者文字 */
  title: string
  messages: ChatMessage[]
  /** 對話訊息區上次捲動位置（依 session 還原） */
  messagesScrollTop?: number
  updatedAt: number
  /** 此對話是否正在執行流程 */
  isRunning: boolean
  /** 使用者按下停止的時間（ms）；用於避免延遲狀態回補造成 UI 反跳 */
  cancelRequestedAt?: number
  /** 串流是否已收到第一筆事件 */
  streamPrimed: boolean
  /** 此對話任務是否已完成（完成後鎖住輸入） */
  taskCompleted: boolean
  /** 若上次是手動停止，下一次送出前先清空舊訊息 */
  clearOnNextSend: boolean
  /** 此對話所屬工具 ID（undefined 表示一般對話） */
  toolId?: string
  /** 從生成圖開啟的討論串：固定使用同一張參考圖，不允許改圖 */
  imageThreadLocked?: boolean
  /** 此 session 綁定的參考圖檔名（存在 uploads/<sessionId>.jpg） */
  referenceImageName?: string
  /** 子討論串來源鍵（同一主 session + 同一泡泡圖片只建立一次） */
  threadSourceKey?: string
  /** 已上傳的附件文件清單（最多 3 個），重新整理後清除 */
  docFileNames?: { serverFilename: string; originalName: string }[]
  /** 使用中的工具級風格偏好（none 代表不使用） */
  selectedStyleProfileId?: string
  /** 最後接收到的 run-stream SSE 事件序號（用於斷線/刷新續傳） */
  lastRunEventSeq?: number
  /** 電商工具：自動產滿九張 vs 階段二後選圖 */
  imageGenerationMode?: "auto" | "select"
  /** 電商工具：階段二已完成、等待使用者勾選要產的 P */
  awaitingStage3Selection?: boolean
}

export const DEFAULT_SESSION_TITLE = "AI 電商圖文助手"
