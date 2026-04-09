import { useEffect, useRef } from "react"
import type { ChatMessage } from "../api"
import { MessageBubble } from "./Message"

type Props = {
  messages: ChatMessage[]
  /** 串流進行中且尚未收到任何後端事件時，顯示簡短連線提示（不使用三點動畫佔滿） */
  streaming: boolean
  streamPrimed: boolean
}

export function ChatWindow({ messages, streaming, streamPrimed }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, streaming, streamPrimed])

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
        {streaming && !streamPrimed ? (
          <div className="msg-row msg-row--assistant">
            <p className="msg-stream-hint">已連線，等待階段輸出…</p>
          </div>
        ) : null}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
