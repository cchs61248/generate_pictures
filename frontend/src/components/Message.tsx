import type { ChatMessage } from "../api"

type Props = {
  message: ChatMessage
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === "user"

  if (isUser) {
    return (
      <div className="msg-row msg-row--user">
        <div className="msg-user-stack">
          <div className="msg-bubble msg-bubble--user">
            {message.imagePreview ? (
              <div className="msg-user-inline-img-wrap">
                <img
                  src={message.imagePreview}
                  alt=""
                  className="msg-user-inline-img"
                  loading="lazy"
                  decoding="async"
                />
              </div>
            ) : null}
            {message.text ? (
              <p className="msg-text msg-text--user">{message.text}</p>
            ) : null}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="msg-row msg-row--assistant">
      <div
        className={`msg-bubble msg-bubble--assistant ${message.error ? "msg-bubble--error" : ""}`}
      >
        <span className="msg-label msg-label--assistant">AI</span>
        {message.text ? <p className="msg-text">{message.text}</p> : null}
        {message.generatedImages && message.generatedImages.length > 0 ? (
          <div className="msg-generated">
            <p className="msg-generated-title">產生的圖片</p>
            <div className="msg-generated-grid">
              {message.generatedImages.map((url) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="msg-generated-link"
                >
                  <img src={url} alt="" className="msg-generated-img" />
                </a>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
