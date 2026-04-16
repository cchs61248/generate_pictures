type FrontendLogLevel = "info" | "warning" | "error" | "debug"

const MAX_MESSAGE_LENGTH = 4000

function trimSlash(url: string): string {
  return url.replace(/\/+$/, "")
}

function clip(text: string): string {
  if (text.length <= MAX_MESSAGE_LENGTH) return text
  return `${text.slice(0, MAX_MESSAGE_LENGTH - 3)}...`
}

export function logFrontend(
  baseUrl: string,
  level: FrontendLogLevel,
  message: string,
  context?: Record<string, unknown>,
): void {
  const payload = JSON.stringify({
    level,
    message: clip(message),
    context: context ?? null,
  })
  const endpoint = `${trimSlash(baseUrl)}/frontend-log`

  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    const blob = new Blob([payload], { type: "application/json" })
    navigator.sendBeacon(endpoint, blob)
    return
  }

  fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
    keepalive: true,
  }).catch(() => {
    // 前端記錄失敗不影響主要流程
  })
}
