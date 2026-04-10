import {
  type MutableRefObject,
  useEffect,
  useLayoutEffect,
  useRef,
} from "react"
import type { ChatMessage } from "../api"
import { getToolById } from "../tools"
import { MessageBubble } from "./Message"

type Props = {
  sessionId: string
  /** 若未曾捲動過可為 undefined，切換回來時改為捲到底 */
  savedScrollTop: number | undefined
  /** 捲動時延遲寫入，避免每幀 setState／localStorage */
  scheduleScrollTopPersist: (sessionId: string, scrollTop: number) => void
  /** 切換對話前立即寫入 */
  persistScrollTopNow: (sessionId: string, scrollTop: number) => void
  /** 供父層在切換／新建對話前強制寫入目前捲動位置 */
  scrollFlushRef: MutableRefObject<() => void>
  messages: ChatMessage[]
  /** 串流進行中且尚未收到任何後端事件時，顯示簡短連線提示（不使用三點動畫佔滿） */
  streaming: boolean
  streamPrimed: boolean
  /** 此對話所屬的工具 ID */
  toolId?: string
  onOpenImageThread?: (
    imageUrl: string,
    bubbleTitle: string,
    sourceKey: string,
  ) => void
}

export function ChatWindow({
  sessionId,
  savedScrollTop,
  scheduleScrollTopPersist,
  persistScrollTopNow,
  scrollFlushRef,
  messages,
  streaming,
  streamPrimed,
  toolId,
  onOpenImageThread,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const messagesElRef = useRef<HTMLDivElement>(null)
  const prevSessionIdRef = useRef<string | null>(null)
  const prevAiSignatureRef = useRef<string>("")
  const scrollSaveRaf = useRef<number | null>(null)
  // 用 ref 追蹤「最後一次 scroll 事件記錄的 scrollTop」，
  // 避免 cleanup 讀 DOM 時被下一個 session 的 layoutEffect 改掉
  const lastScrollTopRef = useRef<number>(0)

  useLayoutEffect(() => {
    scrollFlushRef.current = () => {
      persistScrollTopNow(sessionId, lastScrollTopRef.current)
    }
    return () => {
      // cleanup 直接讀 ref，不讀 DOM，避免受其他 layoutEffect 干擾
      persistScrollTopNow(sessionId, lastScrollTopRef.current)
      scrollFlushRef.current = () => {}
    }
  }, [persistScrollTopNow, scrollFlushRef, sessionId])

  useLayoutEffect(() => {
    const el = messagesElRef.current
    if (!el) return
    if (prevSessionIdRef.current === sessionId) return
    prevSessionIdRef.current = sessionId

    const apply = () => {
      const inner = messagesElRef.current
      if (!inner) return
      if (savedScrollTop === undefined) {
        inner.scrollTop = Math.max(
          0,
          inner.scrollHeight - inner.clientHeight,
        )
      } else {
        const max = Math.max(0, inner.scrollHeight - inner.clientHeight)
        inner.scrollTop = Math.min(savedScrollTop, max)
      }
      // 同步更新 ref，讓 cleanup 拿到正確的初始位置
      lastScrollTopRef.current = inner.scrollTop
    }
    apply()
    requestAnimationFrame(apply)
  }, [sessionId, savedScrollTop])

  useEffect(() => {
    const aiSignature = messages
      .filter((m) => m.role === "assistant")
      .map((m) =>
        JSON.stringify({
          id: m.id,
          text: m.text ?? "",
          lines: m.collapsible?.lines.length ?? 0,
          images: m.generatedImages?.length ?? 0,
          error: Boolean(m.error),
        }),
      )
      .join("|")

    if (prevSessionIdRef.current !== sessionId) {
      prevAiSignatureRef.current = aiSignature
      return
    }

    const prev = prevAiSignatureRef.current
    prevAiSignatureRef.current = aiSignature
    if (aiSignature === prev) return

    const el = messagesElRef.current
    if (!el) return
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, sessionId, streaming, streamPrimed])

  const handleScroll = () => {
    const el = messagesElRef.current
    if (!el) return
    // 立即更新 ref，確保 cleanup 讀到最新值
    lastScrollTopRef.current = el.scrollTop
    if (scrollSaveRaf.current != null) {
      cancelAnimationFrame(scrollSaveRaf.current)
    }
    scrollSaveRaf.current = requestAnimationFrame(() => {
      scrollSaveRaf.current = null
      scheduleScrollTopPersist(sessionId, el.scrollTop)
    })
  }

  return (
    <div className="chat-window">
      <div
        ref={messagesElRef}
        className="chat-messages"
        onScroll={handleScroll}
      >
        <div className="chat-content-inner">
          {messages.length === 0 && (() => {
            const tool = toolId ? getToolById(toolId) : undefined
            return (
              <div className="chat-empty">
                {tool ? (
                  <p className="chat-empty-tool-icon" aria-hidden>{tool.icon}</p>
                ) : null}
                <p className="chat-empty-title">
                  {tool ? tool.chatTitle : "AI 電商圖文助手"}
                </p>
                <p className="chat-empty-hint">
                  {tool
                    ? tool.description
                    : "請先上傳一張商品圖（📎），輸入問題後送出。產生的圖會顯示在對話中。"}
                </p>
              </div>
            )
          })()}
          {messages.map((m) => (
            <MessageBubble
              key={m.id}
              message={m}
              onOpenImageThread={onOpenImageThread}
            />
          ))}
          {streaming ? (
            <div
              className="msg-row msg-row--assistant"
              aria-live="polite"
              aria-label={streamPrimed ? "AI 生成中" : "AI 回應中"}
            >
              <div className="msg-bubble msg-bubble--assistant msg-bubble--typing">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            </div>
          ) : null}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}
