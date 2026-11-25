import { ResearchDegradedBanner } from './ResearchDegradedBanner'
import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import './ChatPanel.css'

/**
 * Lightweight, secure Markdown renderer for chat responses with interactive Node Reference Chips.
 * Renders tables, LaTeX formulae (KaTeX), bold, italics, inline code, code blocks, lists, headings, and clickable node pills.
 */
export function renderMarkdown(text, context = {}) {
  if (!text) return null

  const lines = text.split('\n')
  const elements = []
  let listItems = []
  let inCodeBlock = false
  let codeBlockLines = []
  let codeBlockLang = ''
  let inMathBlock = false
  let mathBlockLines = []
  let tableLines = []

  const flushList = (key) => {
    if (listItems.length > 0) {
      elements.push(
        <ul key={`ul-${key}`} className="chat-markdown-list">
          {listItems.map((item, idx) => (
            <li key={idx}>{renderInlineMarkdown(item, context)}</li>
          ))}
        </ul>,
      )
      listItems = []
    }
  }

  const isTableRow = (l) => {
    const trimmed = l.trim()
    return (
      trimmed.startsWith('|') &&
      trimmed.length > 2 &&
      (trimmed.endsWith('|') || trimmed.includes('|'))
    )
  }

  const isTableSeparator = (l) => {
    const trimmed = l.trim()
    return /^\|?\s*:?-+:?\s*(\|:?-+:?\s*)+\|?$/.test(trimmed)
  }

  const parseCells = (l) => {
    let clean = l.trim()
    if (clean.startsWith('|')) clean = clean.slice(1)
    if (clean.endsWith('|')) clean = clean.slice(0, -1)
    return clean.split(/(?<!\\)\|/).map((c) => c.trim())
  }

  const flushTable = (key) => {
    if (tableLines.length >= 2 && isTableSeparator(tableLines[1])) {
      const headerCells = parseCells(tableLines[0])
      const bodyRows = tableLines
        .slice(2)
        .filter((l) => !isTableSeparator(l))
        .map((l) => {
          const cells = parseCells(l)
          while (cells.length < headerCells.length) {
            cells.push('')
          }
          return cells
        })

      elements.push(
        <div key={`table-wrap-${key}`} className="chat-table-container">
          <table className="chat-markdown-table">
            <thead>
              <tr>
                {headerCells.map((cell, cIdx) => (
                  <th key={`th-${cIdx}`}>{renderInlineMarkdown(cell, context)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, rIdx) => (
                <tr key={`tr-${rIdx}`}>
                  {row.map((cell, cIdx) => (
                    <td key={`td-${rIdx}-${cIdx}`}>{renderInlineMarkdown(cell, context)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      tableLines = []
    } else if (tableLines.length > 0) {
      tableLines.forEach((tLine, tIdx) => {
        elements.push(
          <p key={`table-fallback-${key}-${tIdx}`} className="chat-paragraph">
            {renderInlineMarkdown(tLine, context)}
          </p>,
        )
      })
      tableLines = []
    }
  }

  lines.forEach((line, index) => {
    // Code block toggle
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre
            key={`code-${index}`}
            className="chat-code-block"
            data-language={codeBlockLang || undefined}
          >
            <code>{codeBlockLines.join('\n')}</code>
          </pre>,
        )
        codeBlockLines = []
        codeBlockLang = ''
        inCodeBlock = false
      } else {
        flushList(index)
        flushTable(index)
        inCodeBlock = true
        codeBlockLang = line.trim().slice(3)
      }
      return
    }

    if (inCodeBlock) {
      codeBlockLines.push(line)
      return
    }

    // Multi-line display math block ($$ ... $$)
    if (
      line.trim().startsWith('$$') &&
      (line.trim() === '$$' || !line.trim().slice(2).includes('$$'))
    ) {
      if (inMathBlock) {
        elements.push(
          <div key={`math-block-${index}`} className="chat-math-block">
            {renderInlineMarkdown(`$$${mathBlockLines.join('\n')}$$`, context)}
          </div>,
        )
        mathBlockLines = []
        inMathBlock = false
      } else {
        flushList(index)
        flushTable(index)
        inMathBlock = true
      }
      return
    }

    if (inMathBlock) {
      mathBlockLines.push(line)
      return
    }

    // Single-line display math ($$ ... $$)
    if (line.trim().startsWith('$$') && line.trim().endsWith('$$') && line.trim().length > 4) {
      flushList(index)
      flushTable(index)
      elements.push(
        <div key={`math-block-${index}`} className="chat-math-block">
          {renderInlineMarkdown(line.trim(), context)}
        </div>,
      )
      return
    }

    // Markdown table rows
    if (isTableRow(line)) {
      flushList(index)
      tableLines.push(line)
      return
    }
    flushTable(index)

    // Unordered lists
    const listMatch = line.match(/^(\*|-)\s+(.*)/)
    if (listMatch) {
      listItems.push(listMatch[2])
      return
    }
    flushList(index)

    // Empty line / paragraph break
    if (!line.trim()) {
      return
    }

    // Headings
    if (line.startsWith('### ')) {
      elements.push(
        <h4 key={`h4-${index}`} className="chat-h4">
          {renderInlineMarkdown(line.slice(4), context)}
        </h4>,
      )
      return
    }
    if (line.startsWith('## ')) {
      elements.push(
        <h3 key={`h3-${index}`} className="chat-h3">
          {renderInlineMarkdown(line.slice(3), context)}
        </h3>,
      )
      return
    }

    // Normal paragraph
    elements.push(
      <p key={`p-${index}`} className="chat-paragraph">
        {renderInlineMarkdown(line, context)}
      </p>,
    )
  })

  flushList('final')
  flushTable('final')

  if (inCodeBlock && codeBlockLines.length > 0) {
    elements.push(
      <pre key="code-unclosed" className="chat-code-block">
        <code>{codeBlockLines.join('\n')}</code>
      </pre>,
    )
  }

  if (inMathBlock && mathBlockLines.length > 0) {
    elements.push(
      <div key="math-unclosed" className="chat-math-block">
        {renderInlineMarkdown(`$$${mathBlockLines.join('\n')}$$`, context)}
      </div>,
    )
  }

  return elements
}

/**
 * Parses inline markdown: LaTeX formulae (KaTeX), bold, italics, inline code, and clickable node reference chips.
 */
export function renderInlineMarkdown(str, context = {}) {
  if (!str) return ''
  const { nodeMap, onSelectNode } = context
  const parts = []

  const renderFormula = (tex, isDisplay, key) => {
    try {
      const html = katex.renderToString(tex.trim(), {
        displayMode: isDisplay,
        throwOnError: false,
      })
      return (
        <span
          key={`math-${key}`}
          className={isDisplay ? 'chat-math-display' : 'chat-math-inline'}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )
    } catch {
      return (
        <code key={`math-${key}`} className="chat-math-fallback">
          {tex}
        </code>
      )
    }
  }

  // Auto-close any trailing unclosed math delimiters from truncated stream/LLM outputs
  let cleanStr = str
  const lastParenOpen = cleanStr.lastIndexOf('\\(')
  const lastParenClose = cleanStr.lastIndexOf('\\)')
  if (lastParenOpen !== -1 && lastParenOpen > lastParenClose) {
    cleanStr += '\\)'
  }
  const lastBracketOpen = cleanStr.lastIndexOf('\\[')
  const lastBracketClose = cleanStr.lastIndexOf('\\]')
  if (lastBracketOpen !== -1 && lastBracketOpen > lastBracketClose) {
    cleanStr += '\\]'
  }
  const dollarCount = (cleanStr.match(/\$\$/g) || []).length
  if (dollarCount % 2 !== 0) {
    cleanStr += '$$'
  }

  // Token regex matching (Math placed BEFORE bold/italics so operators like * or _ in formulas aren't consumed):
  // 1) Tagged node: (**Label** )?[node:<uuid>]
  // 2) Bare UUID: [a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}
  // 3) Display math: $$...$$
  // 4) Bracket display math: \[...\]
  // 5) Paren inline math: \(...\)
  // 6) Dollar inline math: $...$ (ignoring currency like $100)
  // 7) Code: `code`
  // 8) Bold: **text**
  // 9) Italics: *text*
  const tokenRegex =
    /(?:(?:\*\*([^*]+)\*\*\s*)?\[node:([a-f0-9-]+)\])|(\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b)|(\$\$[\s\S]+?\$\$)|(\\\[[\s\S]+?\\\])|(\\\([\s\S]+?\\\))|(\$(?!\s|\d+(?:\.\d+)?(?:\s|$))[^$\n]+?(?<!\s)\$)|(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/gi

  let lastIndex = 0
  let match

  while ((match = tokenRegex.exec(cleanStr)) !== null) {
    if (match.index > lastIndex) {
      parts.push(cleanStr.slice(lastIndex, match.index))
    }

    const [
      fullMatch,
      boldLabel,
      taggedUuid,
      bareUuid,
      displayMath,
      bracketDisplayMath,
      parenInlineMath,
      dollarInlineMath,
      codeText,
      boldText,
      italicText,
    ] = match

    // Case 1: Tagged [node:<uuid>] with optional preceding bold name
    if (taggedUuid) {
      const targetNode = nodeMap?.get(String(taggedUuid))
      const displayLabel =
        targetNode?.label ||
        targetNode?.title ||
        targetNode?.data?.label ||
        targetNode?.data?.title ||
        boldLabel ||
        null

      if (targetNode && displayLabel) {
        // State 1: Active node present in current canvas map
        const dotColor =
          targetNode?.background_color ||
          (targetNode?.node_type === 'root'
            ? '#2D5A47'
            : targetNode?.node_type === 'branch'
              ? '#C96B4B'
              : '#899B72')

        parts.push(
          <button
            key={`node-ref-${match.index}`}
            type="button"
            className="chat-node-pill"
            onClick={() => onSelectNode?.(targetNode)}
            title={`Jump to "${displayLabel}" on canvas`}
          >
            <span className="chat-node-pill-dot" style={{ backgroundColor: dotColor }} />
            <span className="chat-node-pill-label">{displayLabel}</span>
          </button>,
        )
      } else if (displayLabel) {
        // State 2: Historical / superseded node reference with known name (e.g. from prior regeneration)
        parts.push(
          <span
            key={`stale-node-${match.index}`}
            className="chat-node-pill chat-node-pill--stale"
            title="This concept was modified or regenerated in a later version of the map"
          >
            <span className="chat-node-pill-dot" style={{ backgroundColor: '#8a928e' }} />
            <span className="chat-node-pill-label">{displayLabel}</span>
          </span>,
        )
      } else {
        // State 3: Unresolved reference without available title
        parts.push(
          <span key={`unresolved-${match.index}`} className="chat-node-unresolved">
            [concept]
          </span>,
        )
      }
    }
    // Case 2: Bare UUID that matches a known node in this mind map
    else if (bareUuid && nodeMap?.has(String(bareUuid))) {
      const targetNode = nodeMap.get(String(bareUuid))
      const displayLabel =
        targetNode?.label ||
        targetNode?.title ||
        targetNode?.data?.label ||
        targetNode?.data?.title ||
        `Concept (${bareUuid.slice(0, 8)})`
      const dotColor =
        targetNode?.background_color ||
        (targetNode?.node_type === 'root'
          ? '#2D5A47'
          : targetNode?.node_type === 'branch'
            ? '#C96B4B'
            : '#899B72')

      parts.push(
        <button
          key={`bare-uuid-${match.index}`}
          type="button"
          className="chat-node-pill"
          onClick={() => onSelectNode?.(targetNode || bareUuid)}
          title={`Jump to "${targetNode?.label || bareUuid}" on canvas`}
        >
          <span className="chat-node-pill-dot" style={{ backgroundColor: dotColor }} />
          <span className="chat-node-pill-label">{displayLabel}</span>
        </button>,
      )
    }
    // Case 3: Display Math ($$ ... $$)
    else if (displayMath) {
      parts.push(renderFormula(displayMath.slice(2, -2), true, match.index))
    }
    // Case 4: Bracket Display Math (\[ ... \])
    else if (bracketDisplayMath) {
      parts.push(renderFormula(bracketDisplayMath.slice(2, -2), true, match.index))
    }
    // Case 5: Paren Inline Math (\( ... \))
    else if (parenInlineMath) {
      parts.push(renderFormula(parenInlineMath.slice(2, -2), false, match.index))
    }
    // Case 6: Dollar Inline Math ($ ... $)
    else if (dollarInlineMath) {
      parts.push(renderFormula(dollarInlineMath.slice(1, -1), false, match.index))
    }
    // Case 7: Inline code
    else if (codeText) {
      parts.push(
        <code key={match.index} className="chat-inline-code">
          {codeText.slice(1, -1)}
        </code>,
      )
    }
    // Case 8: Bold text
    else if (boldText) {
      parts.push(<strong key={match.index}>{boldText.slice(2, -2)}</strong>)
    }
    // Case 9: Italics
    else if (italicText) {
      parts.push(<em key={match.index}>{italicText.slice(1, -1)}</em>)
    }
    // Fallback: output full match as raw text
    else {
      parts.push(fullMatch)
    }

    lastIndex = tokenRegex.lastIndex
  }

  if (lastIndex < cleanStr.length) {
    parts.push(cleanStr.slice(lastIndex))
  }

  return parts
}

/**
 * ChatPanel (S2-FE4 / US-14)
 *
 * Conversational Assistant Drawer with history auto-scroll, markdown rendering,
 * Action Proposal cards (Confirm/Dismiss), selected node context chip,
 * and error/retry banner.
 */
export function ChatPanel({
  isOpen,
  onClose,
  messages = [],
  isSending = false,
  chatError = null,
  onSendMessage,
  onRetry,
  onConfirmAction,
  onDismissAction,
  selectedNode = null,
  nodes = [],
  onSelectNode = null,
}) {
  const [inputText, setInputText] = useState('')
  const [includeNodeContext, setIncludeNodeContext] = useState(true)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  const nodeMap = useMemo(() => {
    const map = new Map()
    if (Array.isArray(nodes)) {
      for (const n of nodes) {
        if (n && n.id) map.set(String(n.id), n)
      }
    }
    return map
  }, [nodes])

  const isValidUrl = useCallback((urlStr) => {
    try {
      const parsed = new URL(urlStr)
      return parsed.protocol === 'http:' || parsed.protocol === 'https:'
    } catch {
      return false
    }
  }, [])

  const getCleanDomain = useCallback((urlStr) => {
    try {
      const parsed = new URL(urlStr)
      return parsed.hostname.replace(/^www\./, '')
    } catch {
      return urlStr
    }
  }, [])

  // Auto-scroll to bottom on new messages or loading state change
  useEffect(() => {
    if (isOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isSending, isOpen])

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus()
    }
  }, [isOpen])

  // Re-enable node context when a new node is selected
  useEffect(() => {
    if (selectedNode) {
      setIncludeNodeContext(true)
    }
  }, [selectedNode])

  if (!isOpen) return null

  const handleFormSubmit = (e) => {
    e.preventDefault()
    if (!inputText.trim() || isSending) return

    const contextNode = includeNodeContext && selectedNode ? selectedNode : null
    onSendMessage?.(inputText.trim(), contextNode)
    setInputText('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleFormSubmit(e)
    }
  }

  const selectedNodeLabel = selectedNode ? selectedNode.label || selectedNode.title : null

  return (
    <aside className="chat-sidebar-drawer" role="complementary" aria-label="Assistant Chat">
      {/* Header */}
      <div className="chat-drawer-header">
        <div className="chat-header-title-wrap">
          <span className="material-symbols-outlined chat-header-icon">psychology</span>
          <div className="chat-header-texts">
            <h3 className="chat-title">Synthesis Assistant</h3>
            <span className="chat-subtitle">Knowledge Chat &amp; Steering</span>
          </div>
        </div>
        <button
          type="button"
          className="btn-close-chat"
          onClick={onClose}
          aria-label="Close chat drawer"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
            close
          </span>
        </button>
      </div>

      {/* Messages List Container */}
      <div className="chat-messages-container" tabIndex={0} aria-label="Conversation History">
        {messages.length === 0 ? (
          <div className="chat-empty-state">
            <span className="material-symbols-outlined chat-empty-icon">forum</span>
            <h4 className="chat-empty-title">Conversational Knowledge Assistant</h4>
            <p className="chat-empty-desc">
              Ask questions about your mind map, request branch expansions, or steer new concepts.
            </p>
            <div className="chat-suggestions-list">
              <button
                type="button"
                className="chat-suggestion-chip"
                onClick={() => setInputText('What are the key concepts and frontiers in this map?')}
              >
                "What are key frontiers in this map?"
              </button>
              <button
                type="button"
                className="chat-suggestion-chip"
                onClick={() =>
                  setInputText('Suggest 3 unexplored research branches for expansion.')
                }
              >
                "Suggest 3 unexplored research branches."
              </button>
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} className={`chat-message-bubble ${msg.role}`}>
              <div className="chat-message-meta">
                <span className="chat-sender-avatar">
                  {msg.role === 'user' ? (
                    'U'
                  ) : (
                    <span className="material-symbols-outlined">auto_awesome</span>
                  )}
                </span>
                <span className="chat-sender-name">
                  {msg.role === 'user' ? 'You' : 'Assistant'}
                </span>
                <span className="chat-message-time">
                  {msg.created_at
                    ? new Date(msg.created_at).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit',
                      })
                    : 'Now'}
                </span>
              </div>

              {/* Message Content */}
              <div className="chat-message-content">
                {renderMarkdown(msg.content, { nodeMap, onSelectNode })}

                {/* S2-FE4: Visual Regeneration Progress Bar */}
                {msg.metadata?.progress_percent !== undefined &&
                  msg.metadata?.execution_status === 'in_progress' && (
                    <div
                      className="chat-regen-progress-bar-wrap"
                      style={{
                        marginTop: '8px',
                        padding: '6px 8px',
                        backgroundColor: 'rgba(21, 94, 99, 0.06)',
                        borderRadius: '6px',
                        border: '1px solid rgba(21, 94, 99, 0.15)',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          fontSize: '11px',
                          fontWeight: 600,
                          color: '#155e63',
                          marginBottom: '4px',
                        }}
                      >
                        <span>{msg.metadata.stage || 'Synthesizing branch'}</span>
                        <span>{msg.metadata.progress_percent}%</span>
                      </div>
                      <div
                        style={{
                          width: '100%',
                          height: '5px',
                          backgroundColor: 'rgba(21, 94, 99, 0.12)',
                          borderRadius: '3px',
                          overflow: 'hidden',
                        }}
                      >
                        <div
                          style={{
                            width: `${Math.min(Math.max(Number(msg.metadata.progress_percent), 5), 100)}%`,
                            height: '100%',
                            backgroundColor: '#155e63',
                            borderRadius: '3px',
                            transition: 'width 0.3s ease',
                          }}
                        />
                      </div>
                    </div>
                  )}
              </div>

              {msg.metadata?.research_warnings?.length > 0 && (
                <ResearchDegradedBanner inline reason={msg.metadata.research_warnings.join(' ')} />
              )}
              {/* S2-L8 / S2-FE4: Citations & Grounding Sources */}
              {((msg.metadata?.cited_node_ids && msg.metadata.cited_node_ids.length > 0) ||
                (msg.metadata?.source_refs && msg.metadata.source_refs.length > 0)) && (
                <div
                  className="chat-citations-section"
                  style={{
                    marginTop: '8px',
                    paddingTop: '8px',
                    borderTop: '1px solid rgba(0,0,0,0.08)',
                  }}
                >
                  {msg.metadata.cited_node_ids && msg.metadata.cited_node_ids.length > 0 && (
                    <div className="chat-cited-nodes" style={{ marginBottom: '6px' }}>
                      <span
                        className="chat-citation-label"
                        style={{
                          fontSize: '11px',
                          fontWeight: 600,
                          color: 'var(--text-muted, #666)',
                        }}
                      >
                        Cited Concepts:
                      </span>
                      <div
                        className="chat-citation-pills"
                        style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '2px' }}
                      >
                        {msg.metadata.cited_node_ids.map((nid) => {
                          const targetNode = nodeMap.get(String(nid))
                          const displayLabel = targetNode?.label || `${String(nid).slice(0, 8)}...`
                          return (
                            <button
                              key={nid}
                              type="button"
                              className="chat-node-pill"
                              onClick={() => onSelectNode?.(targetNode || nid)}
                              title={
                                targetNode
                                  ? `Jump to "${targetNode.label}" (${nid})`
                                  : `Jump to node ${nid}`
                              }
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '4px',
                                padding: '2px 8px',
                                fontSize: '11px',
                                borderRadius: '12px',
                                border: '1px solid #d0d7de',
                                background: '#f6f8fa',
                                cursor: 'pointer',
                              }}
                            >
                              <span
                                className="material-symbols-outlined"
                                style={{ fontSize: '12px' }}
                              >
                                hub
                              </span>
                              <span>{displayLabel}</span>
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {msg.metadata.source_refs && msg.metadata.source_refs.length > 0 && (
                    <div className="chat-source-refs">
                      <span
                        className="chat-citation-label"
                        style={{
                          fontSize: '11px',
                          fontWeight: 600,
                          color: 'var(--text-muted, #666)',
                        }}
                      >
                        Grounding Sources:
                      </span>
                      <ul
                        className="chat-sources-list"
                        style={{ listStyle: 'none', paddingLeft: 0, margin: '2px 0 0 0' }}
                      >
                        {msg.metadata.source_refs.map((src, i) => {
                          const isSafe = isValidUrl(src)
                          const domain = getCleanDomain(src)
                          return (
                            <li key={i} style={{ margin: '2px 0' }}>
                              {isSafe ? (
                                <a
                                  href={src}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="chat-source-link"
                                  title={src}
                                  style={{
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '4px',
                                    fontSize: '11px',
                                    color: '#0969da',
                                    textDecoration: 'none',
                                  }}
                                >
                                  <span
                                    className="material-symbols-outlined"
                                    style={{ fontSize: '12px' }}
                                  >
                                    open_in_new
                                  </span>
                                  <span>{domain}</span>
                                </a>
                              ) : (
                                <span
                                  style={{ fontSize: '11px', color: 'var(--text-muted, #666)' }}
                                >
                                  {src}
                                </span>
                              )}
                            </li>
                          )
                        })}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Action Proposal Card */}
              {msg.proposed_action &&
                (() => {
                  let pa = msg.proposed_action
                  if (typeof pa === 'string') {
                    try {
                      pa = JSON.parse(pa)
                      if (typeof pa === 'string') pa = JSON.parse(pa)
                    } catch {
                      // keep as is
                    }
                  }
                  let meta = msg.metadata || {}
                  if (typeof meta === 'string') {
                    try {
                      meta = JSON.parse(meta)
                    } catch {
                      meta = {}
                    }
                  }
                  const isConfirmed = msg.is_confirmed === true
                  const isDismissed =
                    msg.is_confirmed === false ||
                    meta.execution_status === 'dismissed' ||
                    meta.execution_status === 'rejected'
                  const desc =
                    pa && typeof pa === 'object'
                      ? pa.explanation ||
                        pa.description ||
                        pa.instruction ||
                        'Apply recommended synthesis update.'
                      : 'Apply recommended synthesis update.'

                  return (
                    <div className="action-proposal-card">
                      <div className="action-card-header">
                        <span className="material-symbols-outlined action-badge-icon">
                          auto_fix_high
                        </span>
                        <span className="action-badge-text">Proposed Graph Action</span>
                      </div>
                      <p className="action-card-desc">{desc}</p>

                      <div className="action-card-actions">
                        {isConfirmed ? (
                          <span className="action-status-pill confirmed">
                            <span className="material-symbols-outlined">check_circle</span>
                            <span>Action Confirmed &amp; Applied</span>
                          </span>
                        ) : isDismissed ? (
                          <span className="action-status-pill dismissed">
                            <span className="material-symbols-outlined">cancel</span>
                            <span>Proposal Dismissed</span>
                          </span>
                        ) : (
                          <>
                            <button
                              type="button"
                              className="btn-action-confirm"
                              onClick={() => onConfirmAction?.(msg.id, pa || msg.proposed_action)}
                            >
                              <span className="material-symbols-outlined">check</span>
                              <span>Confirm</span>
                            </button>
                            <button
                              type="button"
                              className="btn-action-dismiss"
                              onClick={() => onDismissAction?.(msg.id)}
                            >
                              <span className="material-symbols-outlined">close</span>
                              <span>Dismiss</span>
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })()}
            </div>
          ))
        )}

        {/* In-Flight Indicator */}
        {isSending && (
          <div className="chat-message-bubble assistant sending">
            <div className="chat-typing-indicator">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
              <span className="typing-text">Assistant is synthesizing...</span>
            </div>
          </div>
        )}

        {/* Error Banner with Retry */}
        {chatError && (
          <div className="chat-error-banner" role="alert">
            <span className="material-symbols-outlined error-icon">error_outline</span>
            <div className="error-body">
              <span className="error-text">{chatError}</span>
              {onRetry && (
                <button type="button" className="btn-chat-retry" onClick={onRetry}>
                  <span className="material-symbols-outlined" style={{ fontSize: '13px' }}>
                    refresh
                  </span>
                  <span>Retry</span>
                </button>
              )}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Context Chip & Input Footer */}
      <div className="chat-drawer-footer">
        {/* 'Ask about this node' Context Chip */}
        {selectedNodeLabel && includeNodeContext && (
          <div className="node-context-chip-wrap">
            <div className="node-context-chip">
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
                adjust
              </span>
              <span className="node-context-label">
                Ask about: <strong>"{selectedNodeLabel}"</strong>
              </span>
              <button
                type="button"
                className="btn-dismiss-context"
                onClick={() => setIncludeNodeContext(false)}
                title="Remove node context"
                aria-label="Remove node context"
              >
                <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>
                  close
                </span>
              </button>
            </div>
          </div>
        )}

        {/* Input Form */}
        <form onSubmit={handleFormSubmit} className="chat-input-form">
          <textarea
            ref={inputRef}
            className="chat-textarea"
            rows={2}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              selectedNodeLabel && includeNodeContext
                ? `Ask about "${selectedNodeLabel}"... (Enter to send)`
                : 'Ask a question or steer knowledge graph... (Enter to send)'
            }
            disabled={isSending}
            aria-label="Chat message"
          />
          <button
            type="submit"
            className="btn-send-message"
            disabled={!inputText.trim() || isSending}
            title="Send message"
            aria-label="Send message"
          >
            <span className="material-symbols-outlined">
              {isSending ? 'hourglass_top' : 'send'}
            </span>
          </button>
        </form>
      </div>
    </aside>
  )
}

export default ChatPanel
