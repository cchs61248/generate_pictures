import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type RefObject,
} from "react"
import { fetchTokenUsage, FALLBACK_MODEL_CHOICES, type TokenUsageRecord } from "../api"

type Props = {
  baseUrl: string
  scrollContainerRef?: RefObject<HTMLElement | null>
  savedMainScrollTop?: number
  savedTableScrollX?: { summary?: number; detail?: number }
  onTableScrollXPersist?: (which: "summary" | "detail", scrollLeft: number) => void
  tableScrollFlushRef?: MutableRefObject<() => void>
  savedDateRange?: { start?: string; end?: string }
  onDateRangeChange?: (next: { start: string; end: string }) => void
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
    ...(FALLBACK_MODEL_CHOICES.TEXT_MODEL ?? []),
    ...(FALLBACK_MODEL_CHOICES.IMAGE_MODEL ?? []),
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
  style_learning_extract: "風格學習",
}

function sourceLabel(source: string): string {
  return SOURCE_LABEL_MAP[source] ?? source
}

/**
 * 官方定價（付費層級・標準方案，每 100 萬 token 的美元費率）
 * 資料來源：https://ai.google.dev/gemini-api/docs/pricing（2026-04-09）
 * 圖片生成模型的 output 費率以圖片 token 費率為準（遠高於文字 token）。
 */
type ModelPricing = {
  inputPer1M: number
  outputPer1M: number
}

const MODEL_PRICING: Record<string, ModelPricing> = {
  // ── 文字模型 ──────────────────────────────────────────────
  // Gemini 3 Flash：$0.50 input / $3.00 output per 1M tokens
  "gemini-3-flash-preview": { inputPer1M: 0.50, outputPer1M: 3.00 },
  // Gemini 3.1 Flash-Lite：$0.25 input / $1.50 output per 1M tokens
  "gemini-3.1-flash-lite-preview": { inputPer1M: 0.25, outputPer1M: 1.50 },
  // Gemini 3.1 Pro：$2.00 input / $12.00 output per 1M tokens（prompt ≤ 200K）
  "gemini-3.1-pro-preview": { inputPer1M: 2.00, outputPer1M: 12.00 },
  // Gemini 2.5 Flash：$0.30 input / $2.50 output per 1M tokens
  "gemini-2.5-flash": { inputPer1M: 0.30, outputPer1M: 2.50 },
  // Gemini 2.5 Pro：$1.25 input / $10.00 output per 1M tokens（prompt ≤ 200K）
  "gemini-2.5-pro": { inputPer1M: 1.25, outputPer1M: 10.00 },
  // ── 圖片生成模型 ───────────────────────────────────────────
  // Nano Banana 2 (gemini-3.1-flash-image-preview)：$0.50 input / $60 output per 1M tokens（圖片 token）
  "gemini-3.1-flash-image-preview": { inputPer1M: 0.50, outputPer1M: 60.00 },
  // Nano Banana Pro (gemini-3-pro-image-preview)：$2.00 input / $120 output per 1M tokens（圖片 token）
  "gemini-3-pro-image-preview": { inputPer1M: 2.00, outputPer1M: 120.00 },
  // Nano Banana (gemini-2.5-flash-image)：$0.30 input / $30 output per 1M tokens（圖片 token）
  "gemini-2.5-flash-image": { inputPer1M: 0.30, outputPer1M: 30.00 },
}

function estimateCost(model: string, inputTokens: number, outputTokens: number): number | null {
  const pricing = MODEL_PRICING[model]
  if (!pricing) return null
  return (inputTokens / 1_000_000) * pricing.inputPer1M
    + (outputTokens / 1_000_000) * pricing.outputPer1M
}

function formatCost(usd: number | null): string {
  if (usd === null) return "—"
  if (usd === 0) return "$0.00"
  if (usd < 0.000001) return "<$0.000001"
  // 顯示有效位數：小額保留更多小數位
  if (usd < 0.01) return `$${usd.toFixed(6)}`
  if (usd < 1) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
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
  cost: number | null
}

const PAGE_SIZE = 10

function totalPages(totalItems: number, pageSize: number): number {
  return Math.max(1, Math.ceil(totalItems / pageSize))
}

function pageSliceStart(page: number, pageSize: number): number {
  return (page - 1) * pageSize
}

function buildSummary(records: TokenUsageRecord[]): ModelSummary[] {
  const map = new Map<string, ModelSummary>()
  for (const r of records) {
    const existing = map.get(r.model)
    const recordCost = estimateCost(r.model, r.input_tokens, r.output_tokens)
    if (existing) {
      existing.input_tokens += r.input_tokens
      existing.output_tokens += r.output_tokens
      existing.count += 1
      if (recordCost !== null) {
        existing.cost = (existing.cost ?? 0) + recordCost
      }
    } else {
      map.set(r.model, {
        model: r.model,
        input_tokens: r.input_tokens,
        output_tokens: r.output_tokens,
        count: 1,
        cost: recordCost,
      })
    }
  }
  return Array.from(map.values()).sort((a, b) => a.model.localeCompare(b.model))
}

export function TokenUsagePage({
  baseUrl,
  scrollContainerRef,
  savedMainScrollTop,
  savedTableScrollX,
  onTableScrollXPersist,
  tableScrollFlushRef,
  savedDateRange,
  onDateRangeChange,
}: Props) {
  const [start, setStart] = useState(
    () => savedDateRange?.start || firstDayOfMonthStr(),
  )
  const [end, setEnd] = useState(
    () => savedDateRange?.end || todayStr(),
  )
  const [records, setRecords] = useState<TokenUsageRecord[]>([])
  const [summaryPage, setSummaryPage] = useState(1)
  const [detailPage, setDetailPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const summaryTableWrapRef = useRef<HTMLDivElement>(null)
  const detailTableWrapRef = useRef<HTMLDivElement>(null)
  const lastSummaryScrollXRef = useRef(0)
  const lastDetailScrollXRef = useRef(0)
  const summaryTableScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  )
  const detailTableScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  )

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchTokenUsage(baseUrl, start, end)
      setRecords(data)
      setSummaryPage(1)
      setDetailPage(1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [baseUrl, start, end])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    onDateRangeChange?.({ start, end })
  }, [end, onDateRangeChange, start])

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
  }, [loading, records, scrollContainerRef, savedMainScrollTop])

  useLayoutEffect(() => {
    if (loading) return
    const s = summaryTableWrapRef.current
    if (s && savedTableScrollX?.summary !== undefined) {
      const apply = () => {
        const inner = summaryTableWrapRef.current
        if (!inner) return
        const max = Math.max(0, inner.scrollWidth - inner.clientWidth)
        inner.scrollLeft = Math.min(savedTableScrollX.summary!, max)
        lastSummaryScrollXRef.current = inner.scrollLeft
      }
      apply()
      requestAnimationFrame(apply)
    }
    const d = detailTableWrapRef.current
    if (d && savedTableScrollX?.detail !== undefined) {
      const apply = () => {
        const inner = detailTableWrapRef.current
        if (!inner) return
        const max = Math.max(0, inner.scrollWidth - inner.clientWidth)
        inner.scrollLeft = Math.min(savedTableScrollX.detail!, max)
        lastDetailScrollXRef.current = inner.scrollLeft
      }
      apply()
      requestAnimationFrame(apply)
    }
  }, [loading, records, savedTableScrollX])

  const scheduleTableScrollXPersist = useCallback(
    (which: "summary" | "detail", scrollLeft: number) => {
      if (which === "summary") {
        if (summaryTableScrollTimerRef.current) {
          clearTimeout(summaryTableScrollTimerRef.current)
        }
        summaryTableScrollTimerRef.current = setTimeout(() => {
          summaryTableScrollTimerRef.current = null
          onTableScrollXPersist?.(which, scrollLeft)
        }, 400)
      } else {
        if (detailTableScrollTimerRef.current) {
          clearTimeout(detailTableScrollTimerRef.current)
        }
        detailTableScrollTimerRef.current = setTimeout(() => {
          detailTableScrollTimerRef.current = null
          onTableScrollXPersist?.(which, scrollLeft)
        }, 400)
      }
    },
    [onTableScrollXPersist],
  )

  useLayoutEffect(() => {
    if (!tableScrollFlushRef || !onTableScrollXPersist) return
    tableScrollFlushRef.current = () => {
      if (summaryTableScrollTimerRef.current) {
        clearTimeout(summaryTableScrollTimerRef.current)
        summaryTableScrollTimerRef.current = null
      }
      if (detailTableScrollTimerRef.current) {
        clearTimeout(detailTableScrollTimerRef.current)
        detailTableScrollTimerRef.current = null
      }
      onTableScrollXPersist("summary", lastSummaryScrollXRef.current)
      onTableScrollXPersist("detail", lastDetailScrollXRef.current)
    }
    return () => {
      tableScrollFlushRef.current = () => {}
    }
  }, [onTableScrollXPersist, tableScrollFlushRef])

  const summary = useMemo(() => buildSummary(records), [records])
  const summaryPageCount = totalPages(summary.length, PAGE_SIZE)
  const safeSummaryPage = Math.min(summaryPage, summaryPageCount)
  const summaryPageStart = pageSliceStart(safeSummaryPage, PAGE_SIZE)
  const summaryPageRows = summary.slice(summaryPageStart, summaryPageStart + PAGE_SIZE)

  const reversedRecords = useMemo(() => [...records].reverse(), [records])
  const detailPageCount = totalPages(reversedRecords.length, PAGE_SIZE)
  const safeDetailPage = Math.min(detailPage, detailPageCount)
  const detailPageStart = pageSliceStart(safeDetailPage, PAGE_SIZE)
  const detailPageRows = reversedRecords.slice(detailPageStart, detailPageStart + PAGE_SIZE)

  const totalInput = records.reduce((s, r) => s + r.input_tokens, 0)
  const totalOutput = records.reduce((s, r) => s + r.output_tokens, 0)
  const totalCost = summary.reduce<number | null>((acc, s) => {
    if (s.cost === null) return acc
    return (acc ?? 0) + s.cost
  }, null)

  return (
    <div className="settings-page settings-page--readable">
      <div className="settings-page-inner">
        <div className="settings-page-intro">
          <h2 className="settings-page-title">Token 用量</h2>
          <p className="settings-page-lead">
            顯示各 Gemini API 呼叫的 Token 消耗與估計費用（僅 API key 模式有數據）。
            費用依 Google Gemini Developer API 付費方案標準定價估算，圖片模型的輸出以圖片 token 費率計算。
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
            <>
              <div
                ref={summaryTableWrapRef}
                className="token-table-wrap"
                onScroll={() => {
                  const el = summaryTableWrapRef.current
                  if (!el) return
                  lastSummaryScrollXRef.current = el.scrollLeft
                  scheduleTableScrollXPersist("summary", el.scrollLeft)
                }}
              >
                <table className="token-table token-table--token-summary">
                  <thead>
                    <tr>
                      <th>模型</th>
                      <th className="token-num">呼叫次數</th>
                      <th className="token-num">Input Token</th>
                      <th className="token-num">Output Token</th>
                      <th className="token-num">Total Token</th>
                      <th className="token-num">估計費用 (USD)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summaryPageRows.map((s) => (
                      <tr key={s.model}>
                        <td className="token-model" title={s.model}>{modelLabel(s.model)}</td>
                        <td className="token-num">{formatNum(s.count)}</td>
                        <td className="token-num">{formatNum(s.input_tokens)}</td>
                        <td className="token-num">{formatNum(s.output_tokens)}</td>
                        <td className="token-num">{formatNum(s.input_tokens + s.output_tokens)}</td>
                        <td className="token-num token-cost">{formatCost(s.cost)}</td>
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
                      <td className="token-num token-cost">{formatCost(totalCost)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <div className="token-pagination">
                <div className="token-pagination-meta">
                  顯示 {summary.length === 0 ? 0 : summaryPageStart + 1}-
                  {Math.min(summaryPageStart + PAGE_SIZE, summary.length)} / 共 {summary.length} 筆
                </div>
                <div className="token-pagination-actions">
                  <button
                    type="button"
                    className="token-page-btn"
                    onClick={() => setSummaryPage((p) => Math.max(1, p - 1))}
                    disabled={safeSummaryPage <= 1}
                  >
                    上一頁
                  </button>
                  <span className="token-page-indicator">
                    第 {safeSummaryPage} / {summaryPageCount} 頁
                  </span>
                  <button
                    type="button"
                    className="token-page-btn"
                    onClick={() => setSummaryPage((p) => Math.min(summaryPageCount, p + 1))}
                    disabled={safeSummaryPage >= summaryPageCount}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            </>
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
            <>
              <div
                ref={detailTableWrapRef}
                className="token-table-wrap"
                onScroll={() => {
                  const el = detailTableWrapRef.current
                  if (!el) return
                  lastDetailScrollXRef.current = el.scrollLeft
                  scheduleTableScrollXPersist("detail", el.scrollLeft)
                }}
              >
                <table className="token-table token-table--token-detail">
                  <thead>
                    <tr>
                      <th>時間（本地）</th>
                      <th>模型</th>
                      <th>來源</th>
                      <th className="token-num">Input</th>
                      <th className="token-num">Output</th>
                      <th className="token-num">費用 (USD)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detailPageRows.map((r, i) => (
                      <tr key={`${r.timestamp}-${r.model}-${r.source}-${detailPageStart + i}`}>
                        <td className="token-ts">{formatTimestamp(r.timestamp)}</td>
                        <td className="token-model" title={r.model}>{modelLabel(r.model)}</td>
                        <td className="token-source" title={r.source}>{sourceLabel(r.source)}</td>
                        <td className="token-num">{formatNum(r.input_tokens)}</td>
                        <td className="token-num">{formatNum(r.output_tokens)}</td>
                        <td className="token-num token-cost">{formatCost(estimateCost(r.model, r.input_tokens, r.output_tokens))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="token-pagination">
                <div className="token-pagination-meta">
                  顯示 {records.length === 0 ? 0 : detailPageStart + 1}-
                  {Math.min(detailPageStart + PAGE_SIZE, records.length)} / 共 {records.length} 筆
                </div>
                <div className="token-pagination-actions">
                  <button
                    type="button"
                    className="token-page-btn"
                    onClick={() => setDetailPage((p) => Math.max(1, p - 1))}
                    disabled={safeDetailPage <= 1}
                  >
                    上一頁
                  </button>
                  <span className="token-page-indicator">
                    第 {safeDetailPage} / {detailPageCount} 頁
                  </span>
                  <button
                    type="button"
                    className="token-page-btn"
                    onClick={() => setDetailPage((p) => Math.min(detailPageCount, p + 1))}
                    disabled={safeDetailPage >= detailPageCount}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
