/** 後端基底 URL，開發時預設 FastAPI uvicorn 埠 8000 */
export function getApiBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL as string | undefined
  return (raw && raw.trim()) || "http://127.0.0.1:8000"
}

export type ChatRole = "user" | "assistant"

/** 折疊區：工具列、系統日誌等 */
export type CollapsibleBlock = {
  id: string
  title: string
  lines: string[]
}

export type ChatMessage = {
  id: string
  role: ChatRole
  text?: string
  /** 文字泡泡格式（預設 plain） */
  textFormat?: "markdown" | "plain"
  /** 可摺疊的工具／階段日誌 */
  collapsible?: CollapsibleBlock
  /** 使用者訊息附圖（建議 data URL，避免 blob 被 revoke 後歷史訊息破圖） */
  imagePreview?: string
  /** 產生圖在後端的 URL（GET /images/...） */
  generatedImages?: string[]
  /** 是否為錯誤訊息 */
  error?: boolean
}

export type StreamEvent =
  | { type: "collapsible_init"; group_id: string; title: string }
  | { type: "collapsible_line"; group_id: string; line: string }
  | { type: "text_block"; format: string; content: string }
  | { type: "image_saved"; sort: number; main: string; saved_file: string }
  | { type: "complete"; saved_files: string[]; final_output_path: string }
  | { type: "error"; detail: string }

export async function uploadImage(
  file: File,
  baseUrl: string,
  sessionId?: string,
): Promise<void> {
  const form = new FormData()
  form.append("file", file)
  if (sessionId) {
    form.append("session_id", sessionId)
  }
  const res = await fetch(`${trimSlash(baseUrl)}/upload-image`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `上傳失敗 (${res.status})`)
  }
}

export async function deleteSessionUpload(
  sessionId: string,
  baseUrl: string,
): Promise<void> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/session-upload/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `刪除上傳圖失敗 (${res.status})`)
  }
}

export type RunResponse = {
  ok: boolean
  final_output_path: string
  saved_files: string[]
}

export type SessionStatePayload = {
  sessions: unknown[]
  activeId: string
}

export async function fetchSessionState(
  baseUrl: string,
): Promise<SessionStatePayload | null> {
  const res = await fetch(`${trimSlash(baseUrl)}/session-state`)
  if (!res.ok) return null
  const data = (await res.json()) as SessionStatePayload
  if (!Array.isArray(data.sessions) || typeof data.activeId !== "string") {
    return null
  }
  return data
}

export async function saveSessionState(
  baseUrl: string,
  payload: SessionStatePayload,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/session-state`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `儲存 session 失敗 (${res.status})`)
  }
}

export async function runGeneration(
  userInput: string,
  baseUrl: string,
  sessionId?: string,
): Promise<RunResponse> {
  const res = await fetch(`${trimSlash(baseUrl)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_input: userInput,
      stage3_only: false,
      session_id: sessionId,
    }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `執行失敗 (${res.status})`)
  }
  return res.json() as Promise<RunResponse>
}

/** 呼叫 POST /run-stream，逐筆解析 SSE data JSON */
export async function consumeRunStream(
  userInput: string,
  baseUrl: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
  sessionId?: string,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/run-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      user_input: userInput,
      stage3_only: false,
      session_id: sessionId,
    }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `串流請求失敗 (${res.status})`)
  }
  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error("無法讀取回應本文")
  }
  const decoder = new TextDecoder()
  let buffer = ""
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split("\n\n")
    buffer = parts.pop() ?? ""
    for (const part of parts) {
      const trimmed = part.trim()
      if (!trimmed.startsWith("data:")) continue
      const jsonStr = trimmed.slice(5).trimStart()
      try {
        onEvent(JSON.parse(jsonStr) as StreamEvent)
      } catch {
        /* 略過無法解析的片段 */
      }
    }
  }
}

/** 將後端回傳的絕對路徑轉成可顯示的圖片 URL */
export function imageUrlsFromSavedFiles(
  savedFiles: string[],
  baseUrl: string,
): string[] {
  const base = trimSlash(baseUrl)
  return savedFiles.map((absPath) => {
    const name = absPath.split(/[/\\]/).pop() ?? absPath
    return `${base}/images/${encodeURIComponent(name)}`
  })
}

function trimSlash(url: string): string {
  return url.replace(/\/+$/, "")
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json()
  } catch {
    return null
  }
}

function extractDetail(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === "string") return d
  }
  return ""
}
