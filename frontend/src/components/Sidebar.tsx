import { useEffect, useMemo, useRef, useState } from "react"
import type { ChatSession } from "../types/chatSession"

type Props = {
  sessions: ChatSession[]
  activeId: string
  onNewChat: () => void
  onSelect: (id: string) => void
  onRename: (id: string, newTitle: string) => void
  onDelete: (id: string) => void
  /** 窄螢幕時關閉抽屜 */
  onNavigate?: () => void
}

export function Sidebar({
  sessions,
  activeId,
  onNewChat,
  onSelect,
  onRename,
  onDelete,
  onNavigate,
}: Props) {
  const [query, setQuery] = useState("")
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState("")
  const renameInputRef = useRef<HTMLInputElement>(null)
  /** Escape 後若觸發 blur，不寫入檔名 */
  const skipRenameBlurRef = useRef(false)

  const sorted = useMemo(() => {
    return [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)
  }, [sessions])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sorted
    return sorted.filter((s) => s.title.toLowerCase().includes(q))
  }, [sorted, query])

  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus()
      renameInputRef.current.select()
    }
  }, [renamingId])

  const handleSelect = (id: string) => {
    if (renamingId) return
    onSelect(id)
    setMenuOpenId(null)
    onNavigate?.()
  }

  const handleNew = () => {
    onNewChat()
    setMenuOpenId(null)
    setRenamingId(null)
    onNavigate?.()
  }

  const applyRenameFromBlur = () => {
    if (skipRenameBlurRef.current) {
      skipRenameBlurRef.current = false
      return
    }
    if (renamingId) {
      onRename(renamingId, renameDraft)
    }
    setRenamingId(null)
  }

  return (
    <aside className="sidebar" aria-label="對話列表">
      <div className="sidebar-top">
        <label className="sidebar-search-wrap">
          <span className="sidebar-search-icon" aria-hidden>
            🔍
          </span>
          <input
            type="search"
            className="sidebar-search"
            placeholder="搜尋對話"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="搜尋對話"
          />
        </label>
      </div>

      <button type="button" className="sidebar-new-chat" onClick={handleNew}>
        <span className="sidebar-new-icon" aria-hidden>
          ✎
        </span>
        <span>新的對話</span>
      </button>

      <p className="sidebar-section-label">對話</p>
      <ul className="sidebar-list">
        {filtered.map((s) => {
          const active = s.id === activeId
          const isRenaming = renamingId === s.id

          if (isRenaming) {
            return (
              <li key={s.id} className="sidebar-item sidebar-item--rename">
                <input
                  ref={renameInputRef}
                  type="text"
                  className="sidebar-rename-input"
                  value={renameDraft}
                  onChange={(e) => setRenameDraft(e.target.value)}
                  aria-label="對話標題"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault()
                      renameInputRef.current?.blur()
                    }
                    if (e.key === "Escape") {
                      e.preventDefault()
                      skipRenameBlurRef.current = true
                      setRenamingId(null)
                    }
                  }}
                  onBlur={applyRenameFromBlur}
                  onClick={(e) => e.stopPropagation()}
                />
              </li>
            )
          }

          return (
            <li key={s.id} className="sidebar-item">
              <button
                type="button"
                className={`sidebar-chat-btn ${active ? "sidebar-chat-btn--active" : ""}`}
                onClick={() => handleSelect(s.id)}
              >
                <span className="sidebar-chat-title">{s.title}</span>
              </button>
              <div className="sidebar-chat-actions">
                <button
                  type="button"
                  className="sidebar-kebab"
                  aria-expanded={menuOpenId === s.id}
                  aria-label="對話選項"
                  onClick={(e) => {
                    e.stopPropagation()
                    setMenuOpenId((id) => (id === s.id ? null : s.id))
                  }}
                >
                  ⋮
                </button>
                {menuOpenId === s.id ? (
                  <div
                    className="sidebar-menu"
                    role="menu"
                    onMouseLeave={() => setMenuOpenId(null)}
                  >
                    <button
                      type="button"
                      role="menuitem"
                      className="sidebar-menu-item"
                      onClick={() => {
                        setMenuOpenId(null)
                        skipRenameBlurRef.current = false
                        setRenamingId(s.id)
                        setRenameDraft(s.title)
                      }}
                    >
                      重新命名
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      className="sidebar-menu-item"
                      onClick={() => {
                        setMenuOpenId(null)
                        onDelete(s.id)
                      }}
                    >
                      刪除對話
                    </button>
                  </div>
                ) : null}
              </div>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
