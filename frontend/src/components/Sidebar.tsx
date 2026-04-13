import { useEffect, useMemo, useRef, useState } from "react"
import type { ChatSession } from "../types/chatSession"
import { TOOLS } from "../tools"

type Props = {
  sessions: ChatSession[]
  activeId: string
  onNewToolChat: (toolId: string) => void
  onSelect: (id: string) => void
  onRename: (id: string, newTitle: string) => void
  onDelete: (id: string) => void
  /** 窄螢幕時關閉抽屜 */
  onNavigate?: () => void
  onOpenSettings: () => void
  settingsActive?: boolean
  onOpenTokenUsage: () => void
  tokenUsageActive?: boolean
}

export function Sidebar({
  sessions,
  activeId,
  onNewToolChat,
  onSelect,
  onRename,
  onDelete,
  onNavigate,
  onOpenSettings,
  settingsActive = false,
  onOpenTokenUsage,
  tokenUsageActive = false,
}: Props) {
  const [query, setQuery] = useState("")
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState("")
  const renameInputRef = useRef<HTMLInputElement>(null)
  const skipRenameBlurRef = useRef(false)
  const [toolsExpanded, setToolsExpanded] = useState(true)
  const [expandedParents, setExpandedParents] = useState<Record<string, boolean>>({})

  const { roots, childMap } = useMemo(() => {
    const rootList: ChatSession[] = []
    const cmap: Record<string, ChatSession[]> = {}
    for (const s of sessions) {
      if (!s.parentId) {
        rootList.push(s)
        continue
      }
      if (!cmap[s.parentId]) cmap[s.parentId] = []
      cmap[s.parentId].push(s)
    }
    return { roots: rootList, childMap: cmap }
  }, [sessions])

  const filteredRoots = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return roots
    return roots.filter((root) => {
      if (root.title.toLowerCase().includes(q)) return true
      return (childMap[root.id] ?? []).some((c) =>
        c.title.toLowerCase().includes(q),
      )
    })
  }, [childMap, query, roots])

  useEffect(() => {
    setExpandedParents((prev) => {
      const next = { ...prev }
      for (const root of roots) {
        if (childMap[root.id]?.length && next[root.id] === undefined) {
          next[root.id] = true
        }
      }
      return next
    })
  }, [childMap, roots])

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

  const handleToolClick = (toolId: string) => {
    onNewToolChat(toolId)
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

  const handleSettings = () => {
    onOpenSettings()
    onNavigate?.()
  }

  const handleTokenUsage = () => {
    onOpenTokenUsage()
    onNavigate?.()
  }

  return (
    <aside className="sidebar" aria-label="對話列表">
      <div className="sidebar-body">
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

        {/* ── Tools（類 Gemini Gem）區塊 ── */}
        <div className="sidebar-gem-section">
          <button
            type="button"
            className="sidebar-gem-header"
            aria-expanded={toolsExpanded}
            onClick={() => setToolsExpanded((v) => !v)}
          >
            <span className="sidebar-gem-header-left">
              <span className="sidebar-gem-icon" aria-hidden>✦</span>
              <span className="sidebar-gem-label">Tools</span>
            </span>
            <span
              className={`sidebar-gem-chevron ${toolsExpanded ? "sidebar-gem-chevron--open" : ""}`}
              aria-hidden
            >
              ›
            </span>
          </button>

          {toolsExpanded && (
            <ul className="sidebar-gem-list" role="list">
              {TOOLS.map((tool) => (
                <li key={tool.id}>
                  <button
                    type="button"
                    className="sidebar-gem-item"
                    title={tool.description}
                    onClick={() => handleToolClick(tool.id)}
                  >
                    <span className="sidebar-gem-item-icon" aria-hidden>
                      {tool.icon}
                    </span>
                    <span className="sidebar-gem-item-name">{tool.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <p className="sidebar-section-label">對話</p>
        <ul className="sidebar-list">
        {filteredRoots.map((s) => {
          const children = childMap[s.id] ?? []
          const rootExpanded = expandedParents[s.id] ?? true
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
            <li key={s.id} className="sidebar-item-group">
              <div className="sidebar-item">
                {children.length > 0 ? (
                  <button
                    type="button"
                    className={`sidebar-tree-toggle ${rootExpanded ? "sidebar-tree-toggle--open" : ""}`}
                    aria-label={rootExpanded ? "收合子討論串" : "展開子討論串"}
                    onClick={() =>
                      setExpandedParents((prev) => ({
                        ...prev,
                        [s.id]: !(prev[s.id] ?? true),
                      }))
                    }
                  >
                    ▾
                  </button>
                ) : (
                  <span className="sidebar-tree-toggle sidebar-tree-toggle--placeholder" />
                )}
                <button
                  type="button"
                  className={`sidebar-chat-btn ${active ? "sidebar-chat-btn--active" : ""}`}
                  onClick={() => handleSelect(s.id)}
                >
                  <span className="sidebar-chat-title-row">
                    {s.isRunning ? (
                      <span
                        className="sidebar-chat-running-dot"
                        aria-label="執行中"
                        title="執行中"
                      />
                    ) : null}
                    {s.toolId ? (
                      <span className="sidebar-chat-tool-badge" aria-hidden>
                        {TOOLS.find((t) => t.id === s.toolId)?.icon ?? "✦"}
                      </span>
                    ) : null}
                    <span className="sidebar-chat-title">{s.title}</span>
                  </span>
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
              </div>
              {children.length > 0 && rootExpanded ? (
                <ul className="sidebar-child-list">
                  {children.map((child) => {
                    const childActive = child.id === activeId
                    return (
                      <li key={child.id} className="sidebar-item sidebar-item--child">
                        <span className="sidebar-child-branch" aria-hidden />
                        <button
                          type="button"
                          className={`sidebar-chat-btn ${childActive ? "sidebar-chat-btn--active" : ""}`}
                          onClick={() => handleSelect(child.id)}
                        >
                          <span className="sidebar-chat-title-row">
                            {child.isRunning ? (
                              <span
                                className="sidebar-chat-running-dot"
                                aria-label="執行中"
                                title="執行中"
                              />
                            ) : null}
                            <span className="sidebar-chat-title">{child.title}</span>
                          </span>
                        </button>
                        <div className="sidebar-chat-actions">
                          <button
                            type="button"
                            className="sidebar-kebab"
                            aria-expanded={menuOpenId === child.id}
                            aria-label="對話選項"
                            onClick={(e) => {
                              e.stopPropagation()
                              setMenuOpenId((id) => (id === child.id ? null : child.id))
                            }}
                          >
                            ⋮
                          </button>
                          {menuOpenId === child.id ? (
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
                                  setRenamingId(child.id)
                                  setRenameDraft(child.title)
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
                                  onDelete(child.id)
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
              ) : null}
            </li>
          )
        })}
        </ul>
      </div>

      <div className="sidebar-footer">
        <button
          type="button"
          className={`sidebar-settings-btn ${tokenUsageActive ? "sidebar-settings-btn--active" : ""}`}
          onClick={handleTokenUsage}
          aria-current={tokenUsageActive ? "page" : undefined}
        >
          <span className="sidebar-settings-icon" aria-hidden>
            📊
          </span>
          <span>Token 用量</span>
        </button>
        <button
          type="button"
          className={`sidebar-settings-btn ${settingsActive ? "sidebar-settings-btn--active" : ""}`}
          onClick={handleSettings}
          aria-current={settingsActive ? "page" : undefined}
        >
          <span className="sidebar-settings-icon" aria-hidden>
            ⚙
          </span>
          <span>設定與說明</span>
        </button>
      </div>
    </aside>
  )
}
