import { useReducer, useCallback, useRef } from 'react'
import { mindMapService } from '../../services/mindMapService'

export const INITIAL_COMMENTS_CHAT_STATE = {
  isChatOpen: false,
  commentsByNodeId: {},
  activeCommentNodeId: null,
  chatThreads: [],
  activeThreadId: null,
  messages: [],
  isSending: false,
  proposedActions: [],
  chatError: null,
  lastFailedMessage: null,
}

export function commentsChatReducer(state, action) {
  switch (action.type) {
    case 'TOGGLE_CHAT':
      return {
        ...state,
        isChatOpen: typeof action.payload === 'boolean' ? action.payload : !state.isChatOpen,
      }
    case 'OPEN_CHAT_FOR_NODE':
      return {
        ...state,
        isChatOpen: true,
        activeCommentNodeId: action.payload,
      }
    case 'ADD_COMMENT': {
      const { nodeId, comment } = action.payload
      return {
        ...state,
        commentsByNodeId: {
          ...state.commentsByNodeId,
          [nodeId]: [...(state.commentsByNodeId[nodeId] || []), comment],
        },
      }
    }
    case 'DELETE_COMMENT': {
      const { nodeId, commentId } = action.payload
      return {
        ...state,
        commentsByNodeId: {
          ...state.commentsByNodeId,
          [nodeId]: (state.commentsByNodeId[nodeId] || []).filter((c) => c.id !== commentId),
        },
      }
    }
    case 'TOGGLE_RESOLVE_COMMENT': {
      const { nodeId, commentId } = action.payload
      return {
        ...state,
        commentsByNodeId: {
          ...state.commentsByNodeId,
          [nodeId]: (state.commentsByNodeId[nodeId] || []).map((c) =>
            c.id === commentId ? { ...c, resolved: !c.resolved } : c,
          ),
        },
      }
    }
    case 'ADD_MESSAGE':
      return {
        ...state,
        messages: [...state.messages, action.payload],
      }
    case 'SET_SENDING':
      return { ...state, isSending: action.payload }
    case 'APPLY_PROPOSED_ACTION':
      return {
        ...state,
        proposedActions: state.proposedActions.filter((a) => a.id !== action.payload.id),
      }
    case 'SET_ERROR':
      return { ...state, chatError: action.payload, isSending: false }
    case 'CLEAR_ERROR':
      return { ...state, chatError: null }
    case 'SET_HISTORY':
      return { ...state, messages: action.payload }
    case 'UPDATE_MESSAGE_CONFIRMATION':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.payload.messageId
            ? { ...m, is_confirmed: action.payload.isConfirmed }
            : m,
        ),
      }
    default:
      return state
  }
}

/**
 * useCommentsChat (FE-2: Comments and Chat, FE-4: Chat Panel)
 *
 * Isolates node comments, active thread state, conversation messages,
 * and proposed actions so FE-2/FE-4 can be developed independently
 * without causing merge conflicts with canvas or export/history.
 */
export function useCommentsChat({ mapId = null, activeNodeId = null } = {}) {
  const [state, dispatch] = useReducer(commentsChatReducer, {
    ...INITIAL_COMMENTS_CHAT_STATE,
    activeCommentNodeId: activeNodeId,
  })

  // Ref to track the last failed message for retry support
  const lastFailedRef = useRef(null)

  const toggleChat = useCallback((forceState) => {
    dispatch({ type: 'TOGGLE_CHAT', payload: forceState })
  }, [])

  const openChatForNode = useCallback((nodeId) => {
    dispatch({ type: 'OPEN_CHAT_FOR_NODE', payload: nodeId })
  }, [])

  const addComment = useCallback((nodeId, commentText, author = 'You') => {
    if (!nodeId || !commentText?.trim()) return null
    const comment = {
      id: `comment-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      nodeId,
      text: commentText.trim(),
      author,
      createdAt: new Date().toISOString(),
    }
    dispatch({ type: 'ADD_COMMENT', payload: { nodeId, comment } })
    return comment
  }, [])

  const deleteComment = useCallback((nodeId, commentId) => {
    dispatch({ type: 'DELETE_COMMENT', payload: { nodeId, commentId } })
  }, [])

  const resolveComment = useCallback((nodeId, commentId) => {
    dispatch({ type: 'TOGGLE_RESOLVE_COMMENT', payload: { nodeId, commentId } })
  }, [])

  const sendChatMessage = useCallback(
    async (text, contextNode = null) => {
      if (!text?.trim()) return null
      dispatch({ type: 'CLEAR_ERROR' })
      dispatch({ type: 'SET_SENDING', payload: true })

      const userMsg = {
        id: `msg-${Date.now()}`,
        role: 'user',
        content: contextNode
          ? `[Re: "${contextNode.label || contextNode.title || 'node'}"] ${text.trim()}`
          : text.trim(),
        created_at: new Date().toISOString(),
        map_id: mapId,
      }
      dispatch({ type: 'ADD_MESSAGE', payload: userMsg })

      // Store for retry support
      lastFailedRef.current = { text, contextNode }

      try {
        const res = await mindMapService.sendChatMessage(mapId, userMsg.content)
        const assistantMsg = res.chat_turn || {
          id: `msg-${Date.now()}-ai`,
          role: 'assistant',
          content: res.message || 'Done.',
          created_at: new Date().toISOString(),
          proposed_action: res.proposed_action || null,
        }
        dispatch({ type: 'ADD_MESSAGE', payload: assistantMsg })
        lastFailedRef.current = null
      } catch (err) {
        dispatch({
          type: 'SET_ERROR',
          payload: err.message || 'Failed to send message. Please retry.',
        })
      } finally {
        dispatch({ type: 'SET_SENDING', payload: false })
      }
    },
    [mapId],
  )

  const retryLastMessage = useCallback(() => {
    if (!lastFailedRef.current) return
    const { text, contextNode } = lastFailedRef.current
    sendChatMessage(text, contextNode)
  }, [sendChatMessage])

  const loadChatHistory = useCallback(
    async (targetMapId) => {
      const id = targetMapId || mapId
      if (!id) return
      try {
        const history = await mindMapService.getChatHistory(id)
        const msgs = Array.isArray(history) ? history : history.messages || []
        dispatch({ type: 'SET_HISTORY', payload: msgs })
      } catch {
        // History loading is non-critical; silently ignore errors
      }
    },
    [mapId],
  )

  const confirmAction = useCallback(async (messageId, proposedAction) => {
    try {
      await mindMapService.confirmProposedAction(messageId, true)
      dispatch({ type: 'UPDATE_MESSAGE_CONFIRMATION', payload: { messageId, isConfirmed: true } })
      return proposedAction
    } catch (err) {
      dispatch({
        type: 'SET_ERROR',
        payload: err.message || 'Failed to confirm action. Please retry.',
      })
      return null
    }
  }, [])

  const dismissAction = useCallback(async (messageId) => {
    try {
      await mindMapService.confirmProposedAction(messageId, false)
      dispatch({
        type: 'UPDATE_MESSAGE_CONFIRMATION',
        payload: { messageId, isConfirmed: false },
      })
    } catch (err) {
      dispatch({
        type: 'SET_ERROR',
        payload: err.message || 'Failed to dismiss action. Please retry.',
      })
    }
  }, [])

  const applyProposedAction = useCallback((action) => {
    if (!action) return
    dispatch({ type: 'APPLY_PROPOSED_ACTION', payload: action })
  }, [])

  return {
    isChatOpen: state.isChatOpen,
    commentsByNodeId: state.commentsByNodeId,
    activeCommentNodeId: state.activeCommentNodeId,
    chatThreads: state.chatThreads,
    activeThreadId: state.activeThreadId,
    messages: state.messages,
    isSending: state.isSending,
    proposedActions: state.proposedActions,
    chatError: state.chatError,
    toggleChat,
    openChatForNode,
    addComment,
    deleteComment,
    resolveComment,
    sendChatMessage,
    retryLastMessage,
    loadChatHistory,
    confirmAction,
    dismissAction,
    applyProposedAction,
  }
}

export default useCommentsChat
