import { File, FileDoc, Image as ImageIcon } from "@phosphor-icons/react"
import { useEffect, useId, useRef } from "react"

const iconDuotone = { weight: "duotone" as const }

const ALLOWED_DOC_EXTS = new Set([".txt", ".pdf", ".docx", ".doc", ".md"])

type DocEntry = { serverFilename: string; originalName: string }

type Props = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  disabled: boolean
  isStreaming: boolean
  taskCompleted: boolean
  /** 目前選取的檔案（單張，再次選取會覆蓋） */
  file: File | null
  /** 已成功上傳的檔名（送出後預覽收回時仍顯示） */
  uploadedFileName: string | null
  previewUrl: string | null
  /** 上傳成功後的 data URL 預覽（與 blob 擇一顯示） */
  inputPreviewDataUrl: string | null
  /** 無 blob／data 時改載入後端 sample.jpg（仍在上傳與送出之間） */
  fallbackSamplePreviewUrl: string | null
  onFileChange: (file: File | null) => void
  uploading: boolean
  lockImageThread?: boolean
  onFallbackSampleStatusChange?: (missing: boolean) => void
  showReferenceMissingWarning?: boolean
  /** 子討論串模式：true 時送出按鈕在無文字時禁用 */
  requireTextToSend?: boolean
  /** 主對話：須已上傳／具備參考圖才可送出 */
  requireReferenceToSend?: boolean
  /** 與 requireReferenceToSend 搭配：後端已就緒（上傳成功或 session 已有參考圖） */
  referenceReady?: boolean
  /** 已上傳的文件清單（最多 3 個） */
  docUploads?: DocEntry[]
  /** 新增一個文件 */
  onAddDoc?: (file: File) => void
  /** 移除指定文件（傳 serverFilename） */
  onRemoveDoc?: (serverFilename: string) => void
  /** 文件正在上傳中 */
  uploadingDoc?: boolean
}

export function InputBar({
  value,
  onChange,
  onSend,
  onStop,
  disabled,
  isStreaming,
  taskCompleted,
  file,
  uploadedFileName,
  previewUrl,
  inputPreviewDataUrl,
  fallbackSamplePreviewUrl,
  onFileChange,
  uploading,
  lockImageThread = false,
  onFallbackSampleStatusChange,
  showReferenceMissingWarning = false,
  requireTextToSend = false,
  requireReferenceToSend = false,
  referenceReady = true,
  docUploads = [],
  onAddDoc,
  onRemoveDoc,
  uploadingDoc = false,
}: Props) {
  const thumbSrc =
    previewUrl ?? inputPreviewDataUrl ?? fallbackSamplePreviewUrl ?? null
  const usingFallbackSample = !previewUrl && !inputPreviewDataUrl && Boolean(fallbackSamplePreviewUrl)
  const showCompleted = taskCompleted && !isStreaming
  const displayText = showCompleted ? "任務完成" : value
  const inputId = useId()
  const docInputId = useId()
  const fileRef = useRef<HTMLInputElement>(null)
  const docFileRef = useRef<HTMLInputElement>(null)

  const handlePick = () => fileRef.current?.click()
  const handleDocPick = () => docFileRef.current?.click()

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) {
      if (!f.type.startsWith("image/")) {
        window.alert("請上傳圖片檔案（JPG、PNG、WebP 等），不支援此檔案格式。")
        e.target.value = ""
        return
      }
      onFileChange(f)
    }
    e.target.value = ""
  }

  const handleDocInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) {
      const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase()
      if (!ALLOWED_DOC_EXTS.has(ext)) {
        window.alert("請上傳 txt、pdf、docx 或 md 格式的文件。")
        e.target.value = ""
        return
      }
      onAddDoc?.(f)
    }
    e.target.value = ""
  }

  const clearFile = () => {
    onFileChange(null)
    if (fileRef.current) fileRef.current.value = ""
  }

  const sendBlocked =
    (requireTextToSend && !value.trim()) ||
    (requireReferenceToSend && !referenceReady)

  useEffect(() => {
    if (!usingFallbackSample) {
      onFallbackSampleStatusChange?.(false)
    }
  }, [onFallbackSampleStatusChange, usingFallbackSample])

  return (
    <div className="input-bar">
      <div className="input-bar-inner">
        {thumbSrc ? (
          <div className="input-preview">
            <img
              src={thumbSrc}
              alt=""
              className="input-preview-img"
              onLoad={() => {
                if (usingFallbackSample) onFallbackSampleStatusChange?.(false)
              }}
              onError={() => {
                if (usingFallbackSample) onFallbackSampleStatusChange?.(true)
              }}
            />
            {lockImageThread ? null : (
              <button
                type="button"
                className="input-preview-remove"
                onClick={clearFile}
                aria-label="移除圖片"
              >
                ×
              </button>
            )}
            {uploading && <span className="input-preview-badge">上傳中…</span>}
          </div>
        ) : null}
        <div className="input-row">
          {/* 圖片上傳 hidden input */}
          <input
            ref={fileRef}
            id={inputId}
            type="file"
            accept="image/*"
            className="input-file-hidden"
            onChange={handleFileInput}
            aria-hidden={true}
            tabIndex={-1}
          />
          {/* 文件上傳 hidden input */}
          <input
            ref={docFileRef}
            id={docInputId}
            type="file"
            accept=".txt,.pdf,.docx,.doc,.md"
            className="input-file-hidden"
            onChange={handleDocInput}
            aria-hidden={true}
            tabIndex={-1}
          />

          {/* 圖片上傳按鈕 */}
          {lockImageThread ? null : (
            <button
              type="button"
              className="input-attach"
              onClick={handlePick}
              disabled={disabled || uploading || showCompleted}
              title="上傳一張商品圖片（再次選取會覆蓋）"
              aria-label="上傳圖片"
            >
              <ImageIcon {...iconDuotone} size={24} aria-hidden />
            </button>
          )}

          <textarea
            className="input-text"
            rows={2}
            placeholder={
              showCompleted
                ? "任務完成"
                : lockImageThread
                  ? "輸入修改指令…（Enter 換行）"
                  : "輸入商品描述、網址或問題…（Enter 換行）"
            }
            value={displayText}
            onChange={(e) => onChange(e.target.value)}
            disabled={disabled || uploading || showCompleted}
          />

          {/* 文件附加按鈕 */}
          {lockImageThread ? null : (
            <button
              type="button"
              className="input-attach input-attach--doc"
              onClick={handleDocPick}
              disabled={disabled || uploadingDoc || showCompleted}
              title={`附加文件（txt/pdf/docx/md，最多 3 個，目前 ${docUploads.length}/3）`}
              aria-label="附加文件"
            >
              <FileDoc {...iconDuotone} size={22} aria-hidden />
              {docUploads.length > 0 && (
                <span className="input-attach-badge">{docUploads.length}</span>
              )}
            </button>
          )}

          <button
            type="button"
            className={isStreaming ? "input-stop" : "input-send"}
            onClick={isStreaming ? onStop : onSend}
            disabled={uploading || showCompleted || (!isStreaming && (disabled || sendBlocked))}
            title={
              !isStreaming && requireReferenceToSend && !referenceReady
                ? "請先上傳一張商品圖"
                : undefined
            }
          >
            {isStreaming ? "停止" : "送出"}
          </button>
        </div>

        {/* 圖片已選提示 */}
        {(file || uploadedFileName) && (
          <p className="input-meta">
            已選：{file?.name ?? uploadedFileName}
          </p>
        )}

        {/* 文件 chip 列表 */}
        {!lockImageThread && docUploads.length > 0 && (
          <div className="input-doc-chips">
            {docUploads.map((doc) => (
              <span key={doc.serverFilename} className="input-doc-chip">
                <File {...iconDuotone} size={14} aria-hidden />
                <span className="input-doc-chip-name" title={doc.originalName}>
                  {doc.originalName}
                </span>
                <button
                  type="button"
                  className="input-doc-chip-remove"
                  onClick={() => onRemoveDoc?.(doc.serverFilename)}
                  aria-label={`移除文件 ${doc.originalName}`}
                >
                  ×
                </button>
              </span>
            ))}
            {uploadingDoc && (
              <span className="input-doc-chip input-doc-chip--uploading">
                上傳中…
              </span>
            )}
          </div>
        )}

        {showReferenceMissingWarning ? (
          <p className="input-meta input-meta--warn">此子串參考圖遺失</p>
        ) : null}
      </div>
    </div>
  )
}
