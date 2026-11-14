import React, { useState, useEffect, useRef } from 'react'
import './ChatPanel.css'

/**
 * Lightweight, secure Markdown renderer for chat responses.
 * Renders bold, italics, inline code, code blocks, lists, headings, and paragraphs.
 */
function renderMarkdown(text) {
  if (!text) return null

  const lines = text.split('\n')
  const elements = []
  let listItems = []
  let inCodeBlock = false
  let codeBlockLines = []
  let codeBlockLang = ''

  const flushList = (key) => {
    if (listItems.length > 0) {
      elements.push(
        <ul key={`ul-${key}`} className="chat-markdown-list">
          {listItems.map((item, idx) => (
            <li key={idx}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>,
      )
      listItems = []
    }
  }

  lines.forEach((line, index) => {
    // Code block toggle
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre key={`code-${index}`} className="chat-code-block">
            <code>{codeBlockLines.join('\n')}</code>
          </pre>,
        )
        codeBlockLines = []
        inCodeBlock = false
      } else {
        flushList(index)
        inCodeBlock = true
        codeBlockLang = line.trim().slice(3)
      }
      return
    }

    if (inCodeBlock) {
      codeBlockLines.push(line)
      return
    }

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
          {renderInlineMarkdown(line.slice(4))}
        </h4>,
      )
      return
    }
    if (line.startsWith('## ')) {
      elements.push(
        <h3 key={`h3-${index}`} className="chat-h3">
          {renderInlineMarkdown(line.slice(3))}
        </h3>,
      )
      return
    }

    // Normal paragraph
    elements.push(
      <p key={`p-${index}`} className="chat-paragraph">
        {renderInlineMarkdown(line)}
      </p>,
    )
  })

  flushList('final')

  if (inCodeBlock && codeBlockLines.length > 0) {
    elements.push(
      <pre key="code-unclosed" className="chat-code-block">
        <code>{codeBlockLines.join('\n')}</code>
      </pre>,
    )
  }

  return elements
}

/**
 * Parses inline markdown: bold, italics, inline code.
 */
function renderInlineMarkdown(str) {
  if (!str) return ''
  const parts = []
  // Regex splitting by bold (**), code (`), and italics (*)
  const regex = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g
  let lastIndex = 0
  let match

  while ((match = regex.exec(str)) !== null) {
    if (match.index > lastIndex) {
      parts.push(str.slice(lastIndex, match.index))
    }
    const token = match[0]
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={match.index}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={match.index} className="chat-inline-code">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (token.startsWith('*') && token.endsWith('*')) {
      parts.push(<em key={match.index}>{token.slice(1, -1)}</em>)
    }
    lastIndex = regex.lastIndex
  }

  if (lastIndex < str.length) {
    parts.push(str.slice(lastIndex))
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
}) {
  const [inputText, setInputText] = useState('')
  const [includeNodeContext, setIncludeNodeContext] = useState(true)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

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
              <div className="chat-message-content">{renderMarkdown(msg.content)}</div>

              {/* Action Proposal Card */}
              {msg.proposed_action && (
                <div className="action-proposal-card">
                  <div className="action-card-header">
                    <span className="material-symbols-outlined action-badge-icon">
                      auto_fix_high
                    </span>
                    <span className="action-badge-text">Proposed Graph Action</span>
                  </div>
                  <p className="action-card-desc">
                    {msg.proposed_action.description || 'Apply recommended synthesis update.'}
                  </p>

                  <div className="action-card-actions">
                    {msg.is_confirmed === true ? (
                      <span className="action-status-pill confirmed">
                        <span className="material-symbols-outlined">check_circle</span>
                        <span>Action Confirmed &amp; Applied</span>
                      </span>
                    ) : msg.is_confirmed === false ? (
                      <span className="action-status-pill dismissed">
                        <span className="material-symbols-outlined">cancel</span>
                        <span>Proposal Dismissed</span>
                      </span>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="btn-action-confirm"
                          onClick={() => onConfirmAction?.(msg.id, msg.proposed_action)}
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
              )}
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
