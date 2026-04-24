import {
  type ChatMessage,
  modelLabel,
  normalizeImageUrlForCurrentApi,
} from "../api"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

type Props = {
  message: ChatMessage
  onOpenImageThread?: (
    imageUrl: string,
    bubbleTitle: string,
    sourceKey: string,
  ) => void
  /** 選圖流程：串流中或流程已鎖定時為 true，禁用勾選與按鈕 */
  planInteractionLocked?: boolean
  onPlanToggleSort?: (messageId: string, sort: number) => void
  onPlanConfirm?: (messageId: string) => void
  onPlanCancel?: (messageId: string) => void
}

export function MessageBubble({
  message,
  onOpenImageThread,
  planInteractionLocked = false,
  onPlanToggleSort,
  onPlanConfirm,
  onPlanCancel,
}: Props) {
  const isUser = message.role === "user"
  const displayModel = message.responseModel
    ? modelLabel(message.responseModel.trim())
    : ""

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
        <span className="msg-label msg-label--assistant">
          AI{displayModel ? ` · ${displayModel}` : ""}
        </span>
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
        {message.planSelection ? (
          <div className="msg-plan-selection">
            {message.planSelection.settled && message.planSelection.cancelled ? (
              <p className="msg-plan-cancelled">已取消產圖。</p>
            ) : null}
            {message.planSelection.settled && !message.planSelection.cancelled ? (
              <>
                <p className="msg-plan-title">已確認產出以下腳本</p>
                {message.planSelection.selectedSorts.length === 0 ? (
                  <p className="msg-plan-cancelled">（無勾選項目）</p>
                ) : (
                  <ul className="msg-plan-settled-list">
                    {[...message.planSelection.selectedSorts]
                      .sort((a, b) => a - b)
                      .map((sort) => {
                        const item = message.planSelection!.items.find(
                          (i) => i.sort === sort,
                        )
                        const label = `P${String(sort).padStart(2, "0")}`
                        return (
                          <li key={sort} className="msg-plan-settled-item">
                            <span className="msg-plan-settled-code">{label}</span>
                            <span className="msg-plan-settled-main">
                              {item?.main?.trim() ? item.main : "—"}
                            </span>
                          </li>
                        )
                      })}
                  </ul>
                )}
              </>
            ) : null}
            {!message.planSelection.settled ? (
              <>
                <p className="msg-plan-title">請勾選要產出的圖片（對應階段二腳本）</p>
                <div className="msg-plan-grid">
                  {message.planSelection.items.map((item) => {
                    const checked =
                      message.planSelection!.selectedSorts.includes(item.sort)
                    return (
                      <label
                        key={item.sort}
                        className={`msg-plan-card${checked ? " msg-plan-card--checked" : ""}`}
                      >
                        <div className="msg-plan-card-head">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={planInteractionLocked}
                            onChange={() =>
                              onPlanToggleSort?.(message.id, item.sort)
                            }
                          />
                          <span className="msg-plan-card-title">
                            P{String(item.sort).padStart(2, "0")}
                          </span>
                        </div>
                        <p className="msg-plan-topic">
                          <span className="msg-plan-topic-label">主題</span>
                          {item.main}
                        </p>
                      </label>
                    )
                  })}
                </div>
                <div className="msg-plan-actions">
                  <button
                    type="button"
                    className="msg-plan-btn msg-plan-btn--primary"
                    disabled={
                      planInteractionLocked ||
                      message.planSelection.selectedSorts.length === 0
                    }
                    onClick={() => onPlanConfirm?.(message.id)}
                  >
                    確認產圖
                  </button>
                  <button
                    type="button"
                    className="msg-plan-btn"
                    disabled={planInteractionLocked}
                    onClick={() => onPlanCancel?.(message.id)}
                  >
                    取消
                  </button>
                </div>
              </>
            ) : null}
          </div>
        ) : null}
        {message.generatedImages && message.generatedImages.length > 0 ? (
          <div className="msg-generated">
            {onOpenImageThread ? (
              <p className="msg-generated-title">產生的圖片</p>
            ) : null}
            <div className="msg-generated-grid">
              {message.generatedImages.map((url, idx) => {
                const displayUrl = normalizeImageUrlForCurrentApi(url)
                return (
                <div key={`${url}::${idx}`} className="msg-generated-card">
                  <a
                    href={displayUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="msg-generated-link"
                  >
                    <img src={displayUrl} alt="" className="msg-generated-img" />
                  </a>
                  {onOpenImageThread ? (
                    <button
                      type="button"
                      className="msg-generated-thread-btn"
                      onClick={() =>
                        onOpenImageThread(
                          displayUrl,
                          (message.text ?? "").trim(),
                          `${message.id}::${idx}`,
                        )
                      }
                    >
                      開啟討論串
                    </button>
                  ) : null}
                </div>
                )
              })}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
