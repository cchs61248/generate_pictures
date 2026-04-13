import { useCallback, useEffect, useState } from "react"
import { fetchTokenUsage, FALLBACK_MODEL_CHOICES, type TokenUsageRecord } from "../api"

type Props = {
  baseUrl: string
}

function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

function firstDayOfMonthStr(): string {
  const d = new Date()
  d.setDate(1)
  return d.toISOString().slice(0, 10)
}

/** 所有模型別名（TEXT + IMAGE 合併） */
const MODEL_LABEL_MAP: Record<string, string> = Object.fromEntries(
  [
    ...( FALLBACK_MODEL_CHOICES.TEXT_MODEL ?? []),
    ...( FALLBACK_MODEL_CHOICES.IMAGE_MODEL ?? []),
  ].map((o) => [o.value, o.label]),
)

function modelLabel(model: string): string {
  return MODEL_LABEL_MAP[model] ?? model
}

const SOURCE_LABEL_MAP: Record<string, string> = {
  stage1_gather: "收集商品資料",
  stage2_json: "生成圖片腳本",
  stage3_image: "生成電商圖片",
  image_thread: "修改圖片",
}

function sourceLabel(source: string): string {
  return SOURCE_LABEL_MAP[source] ?? source
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts)
    const pad = (n: number) => String(n).padStart(2, "0")
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    )
  } catch {
    return ts
  }
}

function formatNum(n: number): string {
  return n.toLocaleString()
}

type ModelSummary = {
  model: string
  input_tokens: number
  output_tokens: number
  count: number
}

function buildSummary(records: TokenUsageRecord[]): ModelSummary[] {
  const map = new Map<string, ModelSummary>()
  for (const r of records) {
    const existing = map.get(r.model)
    if (existing) {
      existing.input_tokens += r.input_tokens
      existing.output_tokens += r.output_tokens
      existing.count += 1
    } else {
      map.set(r.model, {
        model: r.model,
        input_tokens: r.input_tokens,
        output_tokens: r.output_tokens,
        count: 1,
      })
    }
  }
  return Array.from(map.values()).sort((a, b) => a.model.localeCompare(b.model))
}

export function TokenUsagePage({ baseUrl }: Props) {
  const [start, setStart] = useState(firstDayOfMonthStr)
  const [end, setEnd] = useState(todayStr)
  const [records, setRecords] = useState<TokenUsageRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchTokenUsage(baseUrl, start, end)
      setRecords(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [baseUrl, start, end])

  useEffect(() => {
    void load()
  }, [load])

  const summary = buildSummary(records)
  const totalInput = records.reduce((s, r) => s + r.input_tokens, 0)
  const totalOutput = records.reduce((s, r) => s + r.output_tokens, 0)

  return (
    <div className="settings-page">
      <div className="settings-page-inner">
        <div className="settings-page-intro">
          <h2 className="settings-page-title">Token 用量</h2>
          <p className="settings-page-lead">
            顯示各 Gemini API 呼叫的 Token 消耗記錄（僅 API key 模式有數據）。
          </p>
        </div>

        {/* 日期篩選列 */}
        <div className="token-filter-row">
          <label className="token-filter-label">
            起始日期
            <input
              type="date"
              className="token-date-input"
              value={start}
              max={end}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <span className="token-filter-sep">—</span>
          <label className="token-filter-label">
            結束日期
            <input
              type="date"
              className="token-date-input"
              value={end}
              min={start}
              max={todayStr()}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="token-refresh-btn"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading ? "載入中…" : "重新整理"}
          </button>
        </div>

        {error ? (
          <div className="settings-page-error" role="alert">
            {error}
          </div>
        ) : null}

        {/* 彙總表 */}
        <section className="token-section">
          <h3 className="token-section-title">彙總（依模型）</h3>
          {summary.length === 0 ? (
            <p className="settings-page-hint">
              {loading ? "載入中…" : "此區間無資料"}
            </p>
          ) : (
            <div className="token-table-wrap">
              <table className="token-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th className="token-num">呼叫次數</th>
                    <th className="token-num">Input Token</th>
                    <th className="token-num">Output Token</th>
                    <th className="token-num">Total Token</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.map((s) => (
                    <tr key={s.model}>
                      <td className="token-model" title={s.model}>{modelLabel(s.model)}</td>
                      <td className="token-num">{formatNum(s.count)}</td>
                      <td className="token-num">{formatNum(s.input_tokens)}</td>
                      <td className="token-num">{formatNum(s.output_tokens)}</td>
                      <td className="token-num">{formatNum(s.input_tokens + s.output_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="token-total-row">
                    <td>合計</td>
                    <td className="token-num">{formatNum(records.length)}</td>
                    <td className="token-num">{formatNum(totalInput)}</td>
                    <td className="token-num">{formatNum(totalOutput)}</td>
                    <td className="token-num">{formatNum(totalInput + totalOutput)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </section>

        {/* 明細表 */}
        <section className="token-section">
          <h3 className="token-section-title">明細記錄</h3>
          {records.length === 0 ? (
            <p className="settings-page-hint">
              {loading ? "載入中…" : "此區間無資料"}
            </p>
          ) : (
            <div className="token-table-wrap">
              <table className="token-table">
                <thead>
                  <tr>
                    <th>時間（本地）</th>
                    <th>模型</th>
                    <th>來源</th>
                    <th className="token-num">Input</th>
                    <th className="token-num">Output</th>
                  </tr>
                </thead>
                <tbody>
                  {[...records].reverse().map((r, i) => (
                    <tr key={i}>
                      <td className="token-ts">{formatTimestamp(r.timestamp)}</td>
                      <td className="token-model" title={r.model}>{modelLabel(r.model)}</td>
                      <td className="token-source" title={r.source}>{sourceLabel(r.source)}</td>
                      <td className="token-num">{formatNum(r.input_tokens)}</td>
                      <td className="token-num">{formatNum(r.output_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
