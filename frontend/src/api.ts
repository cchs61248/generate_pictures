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
  attachedDocuments?: { originalName: string; serverFilename?: string }[]
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

export async function deleteSessionUploadImage(
  sessionId: string,
  baseUrl: string,
): Promise<void> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/session-upload/${encodeURIComponent(sessionId)}/image`,
    { method: "DELETE" },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `刪除上傳圖檔失敗 (${res.status})`)
  }
}

export async function uploadDocument(
  file: File,
  baseUrl: string,
  sessionId: string,
): Promise<{ ok: boolean; filename: string }> {
  const form = new FormData()
  form.append("file", file)
  form.append("session_id", sessionId)
  const res = await fetch(`${trimSlash(baseUrl)}/upload-document`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `文件上傳失敗 (${res.status})`)
  }
  return res.json() as Promise<{ ok: boolean; filename: string }>
}

export async function deleteSessionDocument(
  sessionId: string,
  filename: string,
  baseUrl: string,
): Promise<void> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/session-upload/${encodeURIComponent(sessionId)}/document/${encodeURIComponent(filename)}`,
    { method: "DELETE" },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `刪除文件失敗 (${res.status})`)
  }
}

export async function deleteSessionDocuments(
  sessionId: string,
  baseUrl: string,
): Promise<void> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/session-upload/${encodeURIComponent(sessionId)}/documents`,
    { method: "DELETE" },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `刪除文件失敗 (${res.status})`)
  }
}

export async function bindSessionImageFromPicture(
  sessionId: string,
  pictureFilename: string,
  baseUrl: string,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/session-upload/from-picture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      picture_filename: pictureFilename,
    }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `綁定討論圖失敗 (${res.status})`)
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
  version: number
  /** 已刪除的 session ID 墓碑列表，用於跨瀏覽器同步刪除 */
  deletedIds?: string[]
}

export async function fetchSessionState(
  baseUrl: string,
): Promise<SessionStatePayload | null> {
  const res = await fetch(`${trimSlash(baseUrl)}/session-state`)
  if (!res.ok) return null
  const data = (await res.json()) as SessionStatePayload
  if (
    !Array.isArray(data.sessions) ||
    typeof data.activeId !== "string" ||
    typeof data.version !== "number"
  ) {
    return null
  }
  return data
}

/** 與 core/config.py 的 ENV_VARS_HIDDEN_FROM_SETTINGS_UI 對齊；設定頁不顯示這些鍵。 */
export const ENV_KEYS_HIDDEN_FROM_SETTINGS_UI = new Set([
  "GEMINI_API_KEY",
  "STAGE3_ONLY_MODE",
  "GEMINI_COOKIE_1PSID",
  "GEMINI_COOKIE_1PSIDTS",
])

export type EnvVariableRow = {
  key: string
  description: string
  value: string
}

export type ModelChoiceOption = {
  value: string
  label: string
}

/** 與 core/config.py 的 TEXT_MODEL_OPTIONS／IMAGE_MODEL_OPTIONS 對齊；GET 未帶 modelChoices 時仍要能顯示下拉 */
export const FALLBACK_MODEL_CHOICES: Record<string, ModelChoiceOption[]> = {
  TEXT_MODEL: [
    { value: "gemini-3-flash-preview", label: "Gemini 3 Flash" },
    { value: "gemini-3.1-flash-lite-preview", label: "Gemini 3.1 Flash-Lite" },
    { value: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro" },
    { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
    { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  ],
  IMAGE_MODEL: [
    { value: "gemini-3.1-flash-image-preview", label: "Nano Banana 2" },
    { value: "gemini-3-pro-image-preview", label: "Nano Banana Pro" },
    { value: "gemini-2.5-flash-image", label: "Nano Banana" },
  ],
}

/** 優先使用後端回傳的 modelChoices，缺漏或空陣列時用 FALLBACK_MODEL_CHOICES */
export function resolveModelChoices(
  key: string,
  api: Record<string, ModelChoiceOption[]> | undefined,
): ModelChoiceOption[] {
  const list = api?.[key]
  if (list && list.length > 0) {
    return list
  }
  return FALLBACK_MODEL_CHOICES[key] ?? []
}

export type EnvSettingsResponse = {
  variables: EnvVariableRow[]
  /** TEXT_MODEL、IMAGE_MODEL 等：官方 model 代碼與介面顯示名稱 */
  modelChoices?: Record<string, ModelChoiceOption[]>
}

export async function fetchEnvSettings(
  baseUrl: string,
): Promise<EnvSettingsResponse> {
  const res = await fetch(`${trimSlash(baseUrl)}/settings/env`)
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `讀取設定失敗 (${res.status})`)
  }
  const data = (await res.json()) as Record<string, unknown>
  const variables = Array.isArray(data.variables)
    ? (data.variables as EnvVariableRow[])
    : []
  const rawChoices = data.modelChoices ?? data.model_choices
  const modelChoices =
    rawChoices &&
    typeof rawChoices === "object" &&
    !Array.isArray(rawChoices)
      ? (rawChoices as Record<string, ModelChoiceOption[]>)
      : undefined
  return { variables, modelChoices }
}

export async function saveEnvSettings(
  baseUrl: string,
  values: Record<string, string>,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/settings/env`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `儲存設定失敗 (${res.status})`)
  }
}

export async function saveSessionState(
  baseUrl: string,
  payload: SessionStatePayload & { expectedVersion: number; deletedIds?: string[] },
): Promise<SessionStatePayload> {
  const res = await fetch(`${trimSlash(baseUrl)}/session-state`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    if (res.status === 409) {
      throw new Error("SESSION_STATE_CONFLICT")
    }
    throw new Error(detail || `儲存 session 失敗 (${res.status})`)
  }
  const data = (await res.json()) as SessionStatePayload
  if (
    !Array.isArray(data.sessions) ||
    typeof data.activeId !== "string" ||
    typeof data.version !== "number"
  ) {
    throw new Error("儲存 session 回應格式錯誤")
  }
  return data
}

/** 開啟子討論串時呼叫：將來源圖片路徑寫入記憶系統 */
export async function initImageThread(
  sessionId: string,
  pictureFilename: string,
  baseUrl: string,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/image-thread/init`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, picture_filename: pictureFilename }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `初始化圖片討論串失敗 (${res.status})`)
  }
}

export type ImageThreadStreamEvent =
  | { type: "progress"; content: string }
  | { type: "complete"; text: string; saved_image: string | null }
  | { type: "error"; detail: string }

/** 呼叫 POST /chat/image-thread，逐筆解析 SSE data JSON */
export async function consumeImageThreadStream(
  sessionId: string,
  userText: string,
  sessionTitle: string,
  selectedStyleProfileId: string,
  baseUrl: string,
  onEvent: (event: ImageThreadStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/chat/image-thread`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      session_id: sessionId,
      user_text: userText,
      session_title: sessionTitle,
      selected_style_profile_id:
        selectedStyleProfileId && selectedStyleProfileId !== "none"
          ? selectedStyleProfileId
          : null,
    }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `圖片討論串請求失敗 (${res.status})`)
  }
  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error("無法讀取回應本文")
  }
  const decoder = new TextDecoder()
  let buffer = ""
  const dispatchSseBlock = (block: string) => {
    const trimmed = block.trim()
    if (!trimmed.startsWith("data:")) return
    const jsonStr = trimmed.slice(5).trimStart()
    try {
      onEvent(JSON.parse(jsonStr) as ImageThreadStreamEvent)
    } catch {
      /* 略過無法解析的片段 */
    }
  }
  while (true) {
    const { done, value } = await reader.read()
    if (value) buffer += decoder.decode(value, { stream: true })
    const segments = buffer.split("\n\n")
    buffer = segments.pop() ?? ""
    for (const seg of segments) {
      dispatchSseBlock(seg)
    }
    if (done) break
  }
  // 最後一段可能沒有尾端 \n\n，導致 complete／error 留在 buffer，await 永不結束
  const tail = buffer.trim()
  if (tail) {
    for (const line of tail.split("\n")) {
      const t = line.trim()
      if (t) dispatchSseBlock(t)
    }
  }
}

export async function runGeneration(
  userInput: string,
  baseUrl: string,
  sessionId?: string,
  selectedStyleProfileId?: string,
): Promise<RunResponse> {
  const res = await fetch(`${trimSlash(baseUrl)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_input: userInput,
      stage3_only: false,
      session_id: sessionId,
      selected_style_profile_id:
        selectedStyleProfileId && selectedStyleProfileId !== "none"
          ? selectedStyleProfileId
          : null,
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
  selectedStyleProfileId?: string,
): Promise<void> {
  const res = await fetch(`${trimSlash(baseUrl)}/run-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      user_input: userInput,
      stage3_only: false,
      session_id: sessionId,
      selected_style_profile_id:
        selectedStyleProfileId && selectedStyleProfileId !== "none"
          ? selectedStyleProfileId
          : null,
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
  const dispatchSseBlock = (block: string) => {
    const trimmed = block.trim()
    if (!trimmed.startsWith("data:")) return
    const jsonStr = trimmed.slice(5).trimStart()
    try {
      onEvent(JSON.parse(jsonStr) as StreamEvent)
    } catch {
      /* 略過無法解析的片段 */
    }
  }
  while (true) {
    const { done, value } = await reader.read()
    if (value) buffer += decoder.decode(value, { stream: true })
    const segments = buffer.split("\n\n")
    buffer = segments.pop() ?? ""
    for (const seg of segments) {
      dispatchSseBlock(seg)
    }
    if (done) break
  }
  const tail = buffer.trim()
  if (tail) {
    for (const line of tail.split("\n")) {
      const t = line.trim()
      if (t) dispatchSseBlock(t)
    }
  }
}

// ── Token 用量 ─────────────────────────────────────────────────────────────────

export type TokenUsageRecord = {
  timestamp: string
  model: string
  source: string
  input_tokens: number
  output_tokens: number
}

export async function fetchTokenUsage(
  baseUrl: string,
  start?: string,
  end?: string,
): Promise<TokenUsageRecord[]> {
  const base = trimSlash(baseUrl)
  const params = new URLSearchParams()
  if (start) params.set("start", start)
  if (end) params.set("end", end)
  const qs = params.toString()
  const url = `${base}/token-usage${qs ? `?${qs}` : ""}`
  const res = await fetch(url)
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `取得 token 用量失敗：HTTP ${res.status}`)
  }
  const data = (await res.json()) as { records: TokenUsageRecord[] }
  return data.records ?? []
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

export type StyleProfile = {
  id: string
  name: string
  summary?: string
  prompt: string
  created_at: string
  source_event_count?: number
  version?: number
}

export type StyleLearningStatus = {
  queue_total: number
  queue_pending_total: number
  queue_extracted_total: number
  profile: {
    version: number
    updated_at: string
    default_profile_id: string
    profiles: StyleProfile[]
  }
}

export type StyleLearningQueueItem = {
  event_id: string
  timestamp: string
  tool_id: string
  session_id: string
  user_text: string
  model_text: string
  image_path?: string | null
  status?: "pending" | "extracted"
  extracted_version?: number | null
  extracted_at?: string | null
}

export type StyleLearningHistoryItem = {
  type: string
  timestamp: string
  [k: string]: unknown
}

export type PagedResult<T> = {
  items: T[]
  page: number
  page_size: number
  total: number
  total_pages: number
}

export async function fetchStyleLearningStatus(
  baseUrl: string,
): Promise<StyleLearningStatus> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/status`,
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `讀取風格學習狀態失敗 (${res.status})`)
  }
  return res.json() as Promise<StyleLearningStatus>
}

export async function fetchStyleLearningQueue(
  baseUrl: string,
  page: number,
  pageSize: number,
  scope: "pending" | "extracted" | "all" = "pending",
): Promise<PagedResult<StyleLearningQueueItem>> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    scope,
  })
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/queue?${params.toString()}`,
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `讀取 queue 失敗 (${res.status})`)
  }
  return res.json() as Promise<PagedResult<StyleLearningQueueItem>>
}

export async function restoreStyleLearningQueue(
  baseUrl: string,
  eventIds: string[],
): Promise<{ restored: number; pending: number; extracted: number }> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/queue/restore`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_ids: eventIds }),
    },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `恢復 queue 失敗 (${res.status})`)
  }
  return res.json() as Promise<{ restored: number; pending: number; extracted: number }>
}

export async function deleteStyleLearningQueue(
  baseUrl: string,
  eventIds: string[],
): Promise<{ deleted: number; remaining: number }> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/queue`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_ids: eventIds }),
    },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `刪除 queue 失敗 (${res.status})`)
  }
  return res.json() as Promise<{ deleted: number; remaining: number }>
}

export async function extractStyleLearning(
  baseUrl: string,
): Promise<{
  ok: boolean
  reason?: string
  queue_before: number
  queue_after: number
}> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/extract`,
    {
      method: "POST",
    },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `觸發萃取失敗 (${res.status})`)
  }
  return res.json() as Promise<{
    ok: boolean
    reason?: string
    queue_before: number
    queue_after: number
  }>
}

export async function rollbackStyleLearning(
  baseUrl: string,
  profileId: string,
): Promise<{ ok: boolean; default_profile_id: string }> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/rollback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId }),
    },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `回滾失敗 (${res.status})`)
  }
  return res.json() as Promise<{ ok: boolean; default_profile_id: string }>
}

export async function deleteStyleProfile(
  baseUrl: string,
  profileId: string,
): Promise<{ ok: boolean; deleted_profile_id: string; default_profile_id: string }> {
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/profile`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId }),
    },
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `刪除偏好失敗 (${res.status})`)
  }
  return res.json() as Promise<{
    ok: boolean
    deleted_profile_id: string
    default_profile_id: string
  }>
}

export async function fetchStyleLearningHistory(
  baseUrl: string,
  page: number,
  pageSize: number,
): Promise<PagedResult<StyleLearningHistoryItem>> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  const res = await fetch(
    `${trimSlash(baseUrl)}/tools/ecommerce-image/style-learning/history?${params.toString()}`,
  )
  if (!res.ok) {
    const body = await safeJson(res)
    throw new Error(extractDetail(body) || `讀取歷史失敗 (${res.status})`)
  }
  return res.json() as Promise<PagedResult<StyleLearningHistoryItem>>
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
