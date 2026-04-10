import { useCallback, useEffect, useState } from "react"
import {
  ENV_KEYS_HIDDEN_FROM_SETTINGS_UI,
  type EnvVariableRow,
  type ModelChoiceOption,
  fetchEnvSettings,
  resolveModelChoices,
  saveEnvSettings,
} from "../api"

type Props = {
  baseUrl: string
}

const GEMINI_BACKEND_OPTIONS = ["apikey", "hybrid"] as const

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

export function SettingsPage({ baseUrl }: Props) {
  const [rows, setRows] = useState<EnvVariableRow[]>([])
  const [modelChoices, setModelChoices] = useState<
    Record<string, ModelChoiceOption[]>
  >({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)

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

  if (loading) {
    return (
      <div className="settings-page">
        <p className="settings-page-hint">載入設定中…</p>
      </div>
    )
  }

  return (
    <div className="settings-page">
      <div className="settings-page-intro">
        <h2 className="settings-page-title">環境變數</h2>
        <p className="settings-page-lead">
          以下欄位會寫入專案根目錄的 <code className="settings-code">.env</code>
          ，並立即套用至目前後端行程；產圖與搜尋等流程都會讀取這裡的值。
        </p>
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
                  setValue(row.key, clampMaxLlmSearchCallsInput(e.target.value))
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
    </div>
  )
}
