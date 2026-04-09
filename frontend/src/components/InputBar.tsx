import { useId, useRef } from "react"

type Props = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  disabled: boolean
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
}

export function InputBar({
  value,
  onChange,
  onSend,
  disabled,
  file,
  uploadedFileName,
  previewUrl,
  inputPreviewDataUrl,
  fallbackSamplePreviewUrl,
  onFileChange,
  uploading,
}: Props) {
  const thumbSrc =
    previewUrl ?? inputPreviewDataUrl ?? fallbackSamplePreviewUrl ?? null
  const inputId = useId()
  const fileRef = useRef<HTMLInputElement>(null)

  const handlePick = () => fileRef.current?.click()

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) {
      onFileChange(f)
    }
    e.target.value = ""
  }

  const clearFile = () => {
    onFileChange(null)
    if (fileRef.current) fileRef.current.value = ""
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (!disabled && !uploading) onSend()
    }
  }

  return (
    <div className="input-bar">
      {thumbSrc ? (
        <div className="input-preview">
          <img src={thumbSrc} alt="" className="input-preview-img" />
          <button
            type="button"
            className="input-preview-remove"
            onClick={clearFile}
            aria-label="移除圖片"
          >
            ×
          </button>
          {uploading && <span className="input-preview-badge">上傳中…</span>}
        </div>
      ) : null}
      <div className="input-row">
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
        <button
          type="button"
          className="input-attach"
          onClick={handlePick}
          disabled={disabled || uploading}
          title="上傳一張圖片（再次選取會覆蓋）"
          aria-label="上傳圖片"
        >
          📎
        </button>
        <textarea
          className="input-text"
          rows={2}
          placeholder="輸入商品描述、網址或問題…（Enter 送出，Shift+Enter 換行）"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || uploading}
        />
        <button
          type="button"
          className="input-send"
          onClick={onSend}
          disabled={disabled || uploading}
        >
          送出
        </button>
      </div>
      {(file || uploadedFileName) && (
        <p className="input-meta">
          已選：{file?.name ?? uploadedFileName}
        </p>
      )}
    </div>
  )
}
