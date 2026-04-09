/** 後端基底 URL，開發時預設 FastAPI uvicorn 埠 8000 */
export function getApiBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL as string | undefined
  return (raw && raw.trim()) || "http://127.0.0.1:8000"
}

export type ChatRole = "user" | "assistant"

export type ChatMessage = {
  id: string
  role: ChatRole
  text?: string
  /** 使用者訊息附圖（建議 data URL，避免 blob 被 revoke 後歷史訊息破圖） */
  imagePreview?: string
  /** 產生圖在後端的 URL（GET /images/...） */
  generatedImages?: string[]
  /** 是否為錯誤訊息 */
  error?: boolean
}

export async function uploadImage(file: File, baseUrl: string): Promise<void> {
  const form = new FormData()
  form.append("file", file)
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

export type RunResponse = {
  ok: boolean
  final_output_path: string
  saved_files: string[]
}

export async function runGeneration(
  userInput: string,
  baseUrl: string,
): Promise<RunResponse> {
  const res = await fetch(`${trimSlash(baseUrl)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_input: userInput,
      stage3_only: false,
    }),
  })
  if (!res.ok) {
    const body = await safeJson(res)
    const detail = extractDetail(body)
    throw new Error(detail || `執行失敗 (${res.status})`)
  }
  return res.json() as Promise<RunResponse>
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
