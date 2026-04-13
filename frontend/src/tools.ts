/**
 * 工具定義清單（類似 Gemini Gems）
 * 每個工具對應一套對話流程，點擊後開啟新對話並以該工具的流程執行
 */

export type ToolDefinition = {
  /** 工具唯一識別碼 */
  id: string
  /** 顯示名稱 */
  name: string
  /** 工具描述（滑鼠懸停時顯示） */
  description: string
  /** 工具圖示（emoji） */
  icon: string
  /** 對話視窗標題 */
  chatTitle: string
  /** 輸入列佔位提示文字 */
  inputPlaceholder?: string
}

export const TOOLS: ToolDefinition[] = [
  {
    id: "ecommerce-image",
    name: "AI 電商圖文助手",
    description: "請先上傳一張商品圖，再輸入商品描述或網址，自動生成9款電商風格圖",
    icon: "🛍️",
    chatTitle: "AI 電商圖文助手",
    inputPlaceholder: "描述商品特色或訴求（可空白，上傳圖片後直接送出）",
  },
]

export function getToolById(id: string): ToolDefinition | undefined {
  return TOOLS.find((t) => t.id === id)
}
