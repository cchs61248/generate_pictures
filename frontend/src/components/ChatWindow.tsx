import { useEffect, useRef } from "react"
import type { ChatMessage } from "../api"
import { MessageBubble } from "./Message"

type Props = {
  messages: ChatMessage[]
  loading: boolean
}

export function ChatWindow({ messages, loading }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, loading])

  return (
    <div className="chat-window">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p className="chat-empty-title">AI 電商圖文助手</p>
            <p className="chat-empty-hint">
              請先上傳一張商品圖（📎），輸入問題後送出。產生的圖會顯示在對話中。
            </p>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {loading && (
          <div className="msg-row msg-row--assistant">
            <div className="msg-bubble msg-bubble--assistant msg-bubble--typing">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
