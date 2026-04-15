import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type RefObject,
} from "react"
import {
  ENV_KEYS_HIDDEN_FROM_SETTINGS_UI,
  deleteStyleProfile,
  deleteStyleLearningQueue,
  extractStyleLearning,
  type EnvVariableRow,
  fetchStyleLearningHistory,
  fetchStyleLearningQueue,
  fetchStyleLearningStatus,
  type ModelChoiceOption,
  restoreStyleLearningQueue,
  renameStyleProfile,
  rollbackStyleLearning,
  type StyleLearningHistoryItem,
  type StyleLearningQueueItem,
  type StyleLearningStatus,
  fetchEnvSettings,
  resolveModelChoices,
  saveEnvSettings,
} from "../api"

type Props = {
  baseUrl: string
  /** 設定頁外層滾動容器（app-main--settings），用於還原垂直捲動 */
  scrollContainerRef?: RefObject<HTMLElement | null>
  savedMainScrollTop?: number
  activeTab?: "env" | "style"
  onTabChange?: (tab: "env" | "style") => void
  onStyleLearningChanged?: () => void
}

const GEMINI_BACKEND_OPTIONS = ["apikey", "hybrid"] as const
const STYLE_PROFILE_LIMIT = 5

function clampMaxLlmSearchCallsInput(raw: string): string {
  const v = raw.trim()
  if (v === "") return ""
  const digits = v.replace(/\D/g, "")
  if (digits === "") return ""
  let n = parseInt(digits, 10)
  if (Number.isNaN(n)) return ""
  n = Math.min(9, Math.max(0, n))
  return String(n)
}

function normalizeGeminiBackend(raw: string): string {
  const t = raw.trim().toLowerCase()
  return GEMINI_BACKEND_OPTIONS.includes(
    t as (typeof GEMINI_BACKEND_OPTIONS)[number],
  )
    ? t
    : "apikey"
}

function modelSelectOptions(
  row: EnvVariableRow,
  choices: ModelChoiceOption[],
): ModelChoiceOption[] {
  const ids = new Set(choices.map((o) => o.value))
  if (row.value && !ids.has(row.value)) {
    return [...choices, { value: row.value, label: `${row.value}（自訂）` }]
  }
  return choices
}

export function SettingsPage({
  baseUrl,
  scrollContainerRef,
  savedMainScrollTop,
  activeTab: activeTabProp,
  onTabChange,
  onStyleLearningChanged,
}: Props) {
  const [activeTab, setActiveTab] = useState<"env" | "style">(activeTabProp ?? "env")
  useEffect(() => {
    if (!activeTabProp) return
    setActiveTab(activeTabProp)
  }, [activeTabProp])

  const [rows, setRows] = useState<EnvVariableRow[]>([])
  const [modelChoices, setModelChoices] = useState<
    Record<string, ModelChoiceOption[]>
  >({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)
  const [styleStatus, setStyleStatus] = useState<StyleLearningStatus | null>(null)
  const [styleQueue, setStyleQueue] = useState<StyleLearningQueueItem[]>([])
  const [queueScope, setQueueScope] = useState<"pending" | "extracted">("pending")
  const [queuePage, setQueuePage] = useState(1)
  const [queueTotalPages, setQueueTotalPages] = useState(1)
  const [queueTotal, setQueueTotal] = useState(0)
  const [selectedQueueIds, setSelectedQueueIds] = useState<Set<string>>(new Set())
  const [historyRows, setHistoryRows] = useState<StyleLearningHistoryItem[]>([])
  const [historyPage, setHistoryPage] = useState(1)
  const [historyTotalPages, setHistoryTotalPages] = useState(1)
  const [styleBusy, setStyleBusy] = useState(false)
  const [styleMsg, setStyleMsg] = useState<string | null>(null)
  const [renamingProfileId, setRenamingProfileId] = useState<string | null>(null)
  const [renameInput, setRenameInput] = useState("")

  const load = useCallback(async () => {
    setError(null)
    setSavedOk(false)
    setLoading(true)
    try {
      const data = await fetchEnvSettings(baseUrl)
      setModelChoices(data.modelChoices ?? {})
      setRows(
        data.variables
          .filter((v) => !ENV_KEYS_HIDDEN_FROM_SETTINGS_UI.has(v.key))
          .map((v) =>
            v.key === "GEMINI_BACKEND"
              ? { ...v, value: normalizeGeminiBackend(v.value) }
              : v.key === "MAX_LLM_SEARCH_CALLS"
                ? { ...v, value: clampMaxLlmSearchCallsInput(v.value) }
                : v,
          ),
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [baseUrl])

  useEffect(() => {
    void load()
  }, [load])

  const loadStyleStatus = useCallback(async () => {
    const data = await fetchStyleLearningStatus(baseUrl)
    setStyleStatus(data)
  }, [baseUrl])

  const loadStyleQueue = useCallback(
    async (page: number, scope = queueScope) => {
      const data = await fetchStyleLearningQueue(baseUrl, page, 10, scope)
      setStyleQueue(data.items)
      setQueuePage(data.page)
      setQueueTotalPages(data.total_pages)
      setQueueTotal(data.total)
      setSelectedQueueIds(new Set())
    },
    [baseUrl, queueScope],
  )

  const loadStyleHistory = useCallback(
    async (page: number) => {
      const data = await fetchStyleLearningHistory(baseUrl, page, 10)
      setHistoryRows(data.items)
      setHistoryPage(data.page)
      setHistoryTotalPages(data.total_pages)
    },
    [baseUrl],
  )

  const refreshStyleAll = useCallback(
    async (queuePageInput = queuePage, historyPageInput = historyPage) => {
      await Promise.all([
        loadStyleStatus(),
        loadStyleQueue(queuePageInput, queueScope),
        loadStyleHistory(historyPageInput),
      ])
    },
    [historyPage, loadStyleHistory, loadStyleQueue, loadStyleStatus, queuePage, queueScope],
  )

  useEffect(() => {
    if (activeTab !== "style") return
    setStyleMsg(null)
    void refreshStyleAll(1, 1)
  }, [activeTab, refreshStyleAll])

  useLayoutEffect(() => {
    if (loading) return
    const el = scrollContainerRef?.current
    if (!el || savedMainScrollTop === undefined) return
    const apply = () => {
      const inner = scrollContainerRef?.current
      if (!inner) return
      const max = Math.max(0, inner.scrollHeight - inner.clientHeight)
      inner.scrollTop = Math.min(savedMainScrollTop, max)
    }
    apply()
    requestAnimationFrame(apply)
  }, [activeTab, historyRows, loading, rows, scrollContainerRef, savedMainScrollTop, styleQueue, styleStatus])

  const setValue = (key: string, value: string) => {
    setRows((prev) =>
      prev.map((r) => (r.key === key ? { ...r, value } : r)),
    )
    setSavedOk(false)
  }

  const handleSave = async () => {
    setError(null)
    setSavedOk(false)
    setSaving(true)
    try {
      const values: Record<string, string> = {}
      for (const r of rows) {
        values[r.key] = r.value
      }
      await saveEnvSettings(baseUrl, values)
      setSavedOk(true)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  const selectedProfile = useMemo(() => {
    const profile = styleStatus?.profile
    if (!profile) return null
    return profile.profiles.find((p) => p.id === profile.default_profile_id) ?? null
  }, [styleStatus])

  const sortedProfiles = useMemo(() => {
    const profile = styleStatus?.profile
    if (!profile) return []
    const defaultId = profile.default_profile_id
    const getCreatedAt = (v?: string) => {
      const t = Date.parse(v ?? "")
      return Number.isNaN(t) ? 0 : t
    }
    return [...profile.profiles].sort((a, b) => {
      if (a.id === defaultId && b.id !== defaultId) return -1
      if (b.id === defaultId && a.id !== defaultId) return 1
      const av = a.version ?? -1
      const bv = b.version ?? -1
      if (av !== bv) return bv - av
      return getCreatedAt(b.created_at) - getCreatedAt(a.created_at)
    })
  }, [styleStatus])

  const handleExtract = async () => {
    const profileCount = styleStatus?.profile.profiles?.length ?? 0
    if (profileCount >= STYLE_PROFILE_LIMIT) {
      window.alert(
        `歷史偏好版本最多只能保留 ${STYLE_PROFILE_LIMIT} 個。\n請先刪除到小於 ${STYLE_PROFILE_LIMIT} 個後，再執行萃取。`,
      )
      return
    }
    setStyleBusy(true)
    setStyleMsg(null)
    setError(null)
    try {
      const result = await extractStyleLearning(baseUrl)
      if (result.ok) {
        setStyleMsg(
          `萃取成功，queue ${result.queue_before} → ${result.queue_after}。`,
        )
      } else {
        setStyleMsg(
          `未執行萃取：${result.reason ?? "未知原因"}（queue ${result.queue_before}）`,
        )
      }
      await refreshStyleAll(1, 1)
      onStyleLearningChanged?.()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setStyleBusy(false)
    }
  }

  const handleDeleteSelectedQueue = async () => {
    if (selectedQueueIds.size === 0) return
    if (!window.confirm(`確定刪除 ${selectedQueueIds.size} 筆 queue？此動作無法復原。`)) {
      return
    }
    setStyleBusy(true)
    setStyleMsg(null)
    setError(null)
    try {
      const res = await deleteStyleLearningQueue(baseUrl, [...selectedQueueIds])
      setStyleMsg(`已永久刪除 ${res.deleted} 筆 queue，剩餘 ${res.remaining} 筆。`)
      await refreshStyleAll(queuePage, historyPage)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setStyleBusy(false)
    }
  }

  const handleRestoreSelectedQueue = async () => {
    if (selectedQueueIds.size === 0) return
    setStyleBusy(true)
    setStyleMsg(null)
    setError(null)
    try {
      const res = await restoreStyleLearningQueue(baseUrl, [...selectedQueueIds])
      setStyleMsg(
        `已恢復 ${res.restored} 筆至待萃取 queue（待萃取 ${res.pending}，已萃取 ${res.extracted}）。`,
      )
      await refreshStyleAll(queuePage, historyPage)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setStyleBusy(false)
    }
  }

  const handleRollback = async (profileId: string) => {
    if (!window.confirm("確定切換到這條歷史風格偏好？")) return
    setStyleBusy(true)
    setStyleMsg(null)
    setError(null)
    try {
      await rollbackStyleLearning(baseUrl, profileId)
      setStyleMsg("已更新預設風格偏好。")
      await refreshStyleAll(queuePage, historyPage)
      onStyleLearningChanged?.()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setStyleBusy(false)
    }
  }

  const handleDeleteProfile = async (profileId: string) => {
    const profiles = styleStatus?.profile.profiles ?? []
    const currentDefaultId = styleStatus?.profile.default_profile_id ?? "none"
    const deleting = profiles.find((p) => p.id === profileId)
    const remain = profiles.filter((p) => p.id !== profileId)
    const fallback =
      currentDefaultId === profileId
        ? (remain.length > 0 ? remain[remain.length - 1] : null)
        : profiles.find((p) => p.id === currentDefaultId) ?? null
    const fallbackText =
      currentDefaultId === profileId
        ? (fallback
            ? `刪除後 default 會回退到：「${fallback.name}」。`
            : "刪除後 default 會回退為：不使用風格偏好（none）。")
        : (fallback
            ? `目前 default 維持為：「${fallback.name}」。`
            : "目前 default 維持為：不使用風格偏好（none）。")
    const deleteName = deleting?.name ?? profileId
    if (
      !window.confirm(
        `確定刪除此歷史風格偏好「${deleteName}」？\n此動作無法復原。\n${fallbackText}`,
      )
    ) {
      return
    }
    setStyleBusy(true)
    setStyleMsg(null)
    setError(null)
    try {
      await deleteStyleProfile(baseUrl, profileId)
      setStyleMsg("已刪除指定歷史風格偏好。")
      await refreshStyleAll(queuePage, historyPage)
      onStyleLearningChanged?.()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setStyleBusy(false)
    }
  }

  const handleStartRename = (profileId: string, oldName: string) => {
    setRenamingProfileId(profileId)
    setRenameInput(oldName)
  }

  const handleCancelRename = () => {
    setRenamingProfileId(null)
    setRenameInput("")
  }

  const handleSubmitRename = async (profileId: string) => {
    const nextName = renameInput.trim()
    if (!nextName) {
      window.alert("名稱不可為空白。")
      return
    }
    setStyleBusy(true)
    setStyleMsg(null)
    setError(null)
    try {
      await renameStyleProfile(baseUrl, profileId, nextName)
      setStyleMsg("已更新歷史偏好名稱。")
      setRenamingProfileId(null)
      setRenameInput("")
      await refreshStyleAll(queuePage, historyPage)
      onStyleLearningChanged?.()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setStyleBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="settings-page">
        <div className="settings-page-inner">
          <p className="settings-page-hint">載入設定中…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="settings-page">
      <div className="settings-page-inner">
        <div className="settings-page-intro">
          <h2 className="settings-page-title">設定與說明</h2>
          <div className="settings-tabs">
            <button
              type="button"
              className={`settings-tab-btn ${activeTab === "env" ? "settings-tab-btn--active" : ""}`}
              onClick={() => {
                setActiveTab("env")
                onTabChange?.("env")
              }}
            >
              環境變數
            </button>
            <button
              type="button"
              className={`settings-tab-btn ${activeTab === "style" ? "settings-tab-btn--active" : ""}`}
              onClick={() => {
                setActiveTab("style")
                onTabChange?.("style")
              }}
            >
              AI 電商圖文助手 風格學習
            </button>
          </div>
        </div>

        {error ? (
          <div className="settings-page-error" role="alert">
            {error}
          </div>
        ) : null}
        {savedOk ? (
          <div className="settings-page-success" role="status">
            已儲存並套用。
          </div>
        ) : null}

        {activeTab === "env" ? (
          <>
            <p className="settings-page-lead">
              以下欄位會寫入專案根目錄的 <code className="settings-code">.env</code>
              ，並立即套用至目前後端行程；產圖與搜尋等流程都會讀取這裡的值。
            </p>
            <ul className="settings-env-list">
              {rows.map((row) => (
                <li key={row.key} className="settings-env-item">
                  <div className="settings-env-meta">
                    <label className="settings-env-key" htmlFor={`env-${row.key}`}>
                      {row.key}
                    </label>
                    <p className="settings-env-desc">{row.description}</p>
                  </div>
                  {row.key === "GEMINI_BACKEND" ? (
                    <select
                      id={`env-${row.key}`}
                      className="settings-env-input settings-env-select"
                      value={normalizeGeminiBackend(row.value)}
                      onChange={(e) => setValue(row.key, e.target.value)}
                      aria-label="GEMINI_BACKEND"
                    >
                      {GEMINI_BACKEND_OPTIONS.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  ) : row.key === "TEXT_MODEL" || row.key === "IMAGE_MODEL" ? (
                    <select
                      id={`env-${row.key}`}
                      className="settings-env-input settings-env-select"
                      value={row.value}
                      onChange={(e) => setValue(row.key, e.target.value)}
                      aria-label={row.key}
                    >
                      {modelSelectOptions(
                        row,
                        resolveModelChoices(row.key, modelChoices),
                      ).map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  ) : row.key === "MAX_LLM_SEARCH_CALLS" ? (
                    <input
                      id={`env-${row.key}`}
                      className="settings-env-input"
                      type="number"
                      min={0}
                      max={9}
                      step={1}
                      inputMode="numeric"
                      pattern="[0-9]*"
                      autoComplete="off"
                      value={row.value}
                      onChange={(e) =>
                        setValue(
                          row.key,
                          clampMaxLlmSearchCallsInput(e.target.value),
                        )
                      }
                    />
                  ) : (
                    <input
                      id={`env-${row.key}`}
                      className="settings-env-input"
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      value={row.value}
                      onChange={(e) => setValue(row.key, e.target.value)}
                    />
                  )}
                </li>
              ))}
            </ul>
            <div className="settings-page-actions">
              <button
                type="button"
                className="settings-save"
                disabled={saving}
                onClick={() => void handleSave()}
              >
                {saving ? "儲存中…" : "儲存"}
              </button>
              <button
                type="button"
                className="settings-reload"
                disabled={saving || loading}
                onClick={() => void load()}
              >
                重新載入
              </button>
            </div>
          </>
        ) : (
          <div className="style-learning-page">
            <p className="settings-page-lead">
              可手動觸發風格萃取，管理待萃取 queue，並查看/回滾工具級歷史風格偏好。
            </p>
            <p className="settings-env-desc">
              手動執行萃取會以 Queue 內容，並同時參考目前使用中的工具風格偏好，產生下一版偏好。
            </p>
            {styleMsg ? (
              <div className="settings-page-success" role="status">
                {styleMsg}
              </div>
            ) : null}
            <div className="settings-page-actions">
              <button
                type="button"
                className="settings-save"
                disabled={styleBusy}
                onClick={() => void handleExtract()}
              >
                {styleBusy ? "處理中…" : "手動執行萃取"}
              </button>
              <button
                type="button"
                className="settings-reload"
                disabled={styleBusy}
                onClick={() => void refreshStyleAll(queuePage, historyPage)}
              >
                重新載入
              </button>
            </div>

            <section className="token-section">
              <h3 className="token-section-title">目前工具級風格偏好</h3>
              <p className="settings-env-desc">
                Queue 總筆數：{styleStatus?.queue_total ?? 0}（待萃取 {styleStatus?.queue_pending_total ?? 0} / 已萃取 {styleStatus?.queue_extracted_total ?? 0}）
              </p>
              {selectedProfile ? (
                <div className="style-current-card">
                  <p><strong>{selectedProfile.name}</strong></p>
                  <p className="settings-env-desc">{selectedProfile.summary || "（無摘要）"}</p>
                  <pre className="style-prompt-preview">{selectedProfile.prompt}</pre>
                </div>
              ) : (
                <p className="settings-page-hint">目前尚未有可用的歷史風格偏好。</p>
              )}
            </section>

            <section className="token-section">
              <h3 className="token-section-title">Queue（每頁 10 筆）</h3>
              <div className="settings-tabs">
                <button
                  type="button"
                  className={`settings-tab-btn ${queueScope === "pending" ? "settings-tab-btn--active" : ""}`}
                  onClick={() => {
                    setQueueScope("pending")
                    void loadStyleQueue(1, "pending")
                  }}
                >
                  待萃取
                </button>
                <button
                  type="button"
                  className={`settings-tab-btn ${queueScope === "extracted" ? "settings-tab-btn--active" : ""}`}
                  onClick={() => {
                    setQueueScope("extracted")
                    void loadStyleQueue(1, "extracted")
                  }}
                >
                  已萃取
                </button>
              </div>
              <div className="style-queue-actions">
                {queueScope === "extracted" ? (
                  <button
                    type="button"
                    className="token-page-btn"
                    disabled={styleBusy || selectedQueueIds.size === 0}
                    onClick={() => void handleRestoreSelectedQueue()}
                  >
                    恢復到待萃取（{selectedQueueIds.size}）
                  </button>
                ) : null}
                <button
                  type="button"
                  className="token-page-btn"
                  disabled={styleBusy || selectedQueueIds.size === 0}
                  onClick={() => void handleDeleteSelectedQueue()}
                >
                  永久刪除（{selectedQueueIds.size}）
                </button>
              </div>
              <div className="token-table-wrap">
                <table className="token-table">
                  <thead>
                    <tr>
                      <th />
                      <th>時間</th>
                      <th>Session</th>
                      <th>使用者提問</th>
                      <th>LLM 回應</th>
                      <th>標記</th>
                    </tr>
                  </thead>
                  <tbody>
                    {styleQueue.map((q) => (
                      <tr key={q.event_id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedQueueIds.has(q.event_id)}
                            onChange={(e) => {
                              setSelectedQueueIds((prev) => {
                                const next = new Set(prev)
                                if (e.target.checked) next.add(q.event_id)
                                else next.delete(q.event_id)
                                return next
                              })
                            }}
                          />
                        </td>
                        <td className="token-ts">{q.timestamp}</td>
                        <td className="token-source">{q.session_id}</td>
                        <td className="style-cell-text">{q.user_text}</td>
                        <td className="style-cell-text">{q.model_text}</td>
                        <td className="token-source">
                          {q.status === "extracted"
                            ? `v${q.extracted_version ?? "-"}`
                            : "pending"}
                        </td>
                      </tr>
                    ))}
                    {styleQueue.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="settings-page-hint">本頁無資料</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              <div className="token-pagination">
                <div className="token-pagination-meta">
                  第 {queuePage} / {queueTotalPages} 頁，共 {queueTotal} 筆
                </div>
                <div className="token-pagination-actions">
                  <button
                    type="button"
                    className="token-page-btn"
                    disabled={queuePage <= 1}
                    onClick={() => void loadStyleQueue(queuePage - 1)}
                  >
                    上一頁
                  </button>
                  <button
                    type="button"
                    className="token-page-btn"
                    disabled={queuePage >= queueTotalPages}
                    onClick={() => void loadStyleQueue(queuePage + 1)}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            </section>

            <section className="token-section">
              <h3 className="token-section-title">回滾與觀測歷史</h3>
              <div className="token-table-wrap">
                <table className="token-table">
                  <thead>
                    <tr>
                      <th>時間</th>
                      <th>類型</th>
                      <th>內容</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyRows.map((h, idx) => (
                      <tr key={`${h.timestamp}-${h.type}-${idx}`}>
                        <td className="token-ts">{String(h.timestamp ?? "")}</td>
                        <td className="token-source">{String(h.type ?? "")}</td>
                        <td className="style-cell-text">{JSON.stringify(h)}</td>
                      </tr>
                    ))}
                    {historyRows.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="settings-page-hint">本頁無資料</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              <div className="token-pagination">
                <div className="token-pagination-meta">
                  第 {historyPage} / {historyTotalPages} 頁
                </div>
                <div className="token-pagination-actions">
                  <button
                    type="button"
                    className="token-page-btn"
                    disabled={historyPage <= 1}
                    onClick={() => void loadStyleHistory(historyPage - 1)}
                  >
                    上一頁
                  </button>
                  <button
                    type="button"
                    className="token-page-btn"
                    disabled={historyPage >= historyTotalPages}
                    onClick={() => void loadStyleHistory(historyPage + 1)}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            </section>

            <section className="token-section">
              <h3 className="token-section-title">歷史偏好列表（可回滾）</h3>
              <div className="style-profile-list">
                {sortedProfiles.map((p) => (
                  <div key={p.id} className="style-profile-item">
                    <div>
                      {renamingProfileId === p.id ? (
                        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                          <input
                            className="settings-env-input"
                            type="text"
                            maxLength={24}
                            value={renameInput}
                            onChange={(e) => setRenameInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault()
                                void handleSubmitRename(p.id)
                              }
                            }}
                            disabled={styleBusy}
                            aria-label="歷史偏好名稱"
                          />
                          <button
                            type="button"
                            className="token-page-btn"
                            disabled={styleBusy}
                            onClick={() => void handleSubmitRename(p.id)}
                          >
                            保存
                          </button>
                          <button
                            type="button"
                            className="token-page-btn"
                            disabled={styleBusy}
                            onClick={handleCancelRename}
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <p><strong>{p.name}</strong></p>
                      )}
                      <p className="settings-env-desc">{p.summary || "（無摘要）"}</p>
                    </div>
                    <div className="style-profile-actions">
                      <button
                        type="button"
                        className="token-page-btn"
                        disabled={styleBusy}
                        onClick={() => handleStartRename(p.id, p.name)}
                      >
                        改名
                      </button>
                      <button
                        type="button"
                        className="token-page-btn"
                        disabled={styleBusy || styleStatus?.profile.default_profile_id === p.id}
                        onClick={() => void handleRollback(p.id)}
                      >
                        {styleStatus?.profile.default_profile_id === p.id ? "目前使用中" : "回滾到此版本"}
                      </button>
                      <button
                        type="button"
                        className="token-page-btn"
                        disabled={styleBusy}
                        onClick={() => void handleDeleteProfile(p.id)}
                      >
                        刪除
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
