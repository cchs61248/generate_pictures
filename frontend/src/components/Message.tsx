import type { ChatMessage } from "../api"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

type Props = {
  message: ChatMessage
  onOpenImageThread?: (
    imageUrl: string,
    bubbleTitle: string,
    sourceKey: string,
  ) => void
}

export function MessageBubble({ message, onOpenImageThread }: Props) {
  const isUser = message.role === "user"

  if (isUser) {
    const hasImage = Boolean(message.imagePreview)
    const docs = message.attachedDocuments ?? []
    const hasDocs = docs.length > 0
    const hasText = Boolean(message.text)

    return (
      <div className="msg-row msg-row--user">
        <div className="msg-user-stack">
          {hasImage ? (
            <div className="msg-bubble msg-bubble--user msg-bubble--user-image">
              <div className="msg-user-inline-img-wrap">
                <img
                  src={message.imagePreview}
                  alt=""
                  className="msg-user-inline-img"
                  loading="lazy"
                  decoding="async"
                />
              </div>
            </div>
          ) : null}
          {hasDocs ? (
            <div className="msg-bubble msg-bubble--user msg-bubble--user-docs">
              <p className="msg-user-docs-title">附件</p>
              <ul className="msg-user-doc-list">
                {docs.map((d, i) => (
                  <li key={`${d.serverFilename ?? d.originalName}-${i}`} className="msg-user-doc-item">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="msg-user-doc-icon"
                      aria-hidden
                    >
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <polyline points="14 2 14 8 20 8" />
                    </svg>
                    <span className="msg-user-doc-name" title={d.originalName}>
                      {d.originalName}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {hasText ? (
            <div className="msg-bubble msg-bubble--user">
              <p className="msg-text msg-text--user">{message.text}</p>
            </div>
          ) : null}
        </div>
      </div>
    )
  }

  const textFormat = message.textFormat ?? "plain"
  const isMd = textFormat === "markdown"

  return (
    <div className="msg-row msg-row--assistant">
      <div
        className={`msg-bubble msg-bubble--assistant ${message.error ? "msg-bubble--error" : ""}`}
      >
        <span className="msg-label msg-label--assistant">AI</span>
        {message.collapsible ? (
          <details className="msg-collapsible">
            <summary className="msg-collapsible-summary">
              <span className="msg-collapsible-title">
                {message.collapsible.title}
              </span>
              <span className="msg-collapsible-meta">
                {message.collapsible.lines.length} 行
              </span>
            </summary>
            <pre className="msg-collapsible-body">
              {message.collapsible.lines.join("\n")}
            </pre>
          </details>
        ) : null}
        {message.text ? (
          <div
            className={
              isMd
                ? "msg-text msg-text--markdown"
                : "msg-text"
            }
          >
            {isMd ? (
              <Markdown remarkPlugins={[remarkGfm]}>{message.text}</Markdown>
            ) : (
              message.text
            )}
          </div>
        ) : null}
        {message.generatedImages && message.generatedImages.length > 0 ? (
          <div className="msg-generated">
            {onOpenImageThread ? (
              <p className="msg-generated-title">產生的圖片</p>
            ) : null}
            <div className="msg-generated-grid">
              {message.generatedImages.map((url, idx) => (
                <div key={url} className="msg-generated-card">
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="msg-generated-link"
                  >
                    <img src={url} alt="" className="msg-generated-img" />
                  </a>
                  {onOpenImageThread ? (
                    <button
                      type="button"
                      className="msg-generated-thread-btn"
                      onClick={() =>
                        onOpenImageThread(
                          url,
                          (message.text ?? "").trim(),
                          `${message.id}::${idx}`,
                        )
                      }
                    >
                      開啟討論串
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
