import { useReducer, useCallback } from 'react'

export const INITIAL_COMMENTS_CHAT_STATE = {
  isChatOpen: false,
  commentsByNodeId: {},
  activeCommentNodeId: null,
  chatThreads: [],
  activeThreadId: null,
  messages: [],
  isSending: false,
  proposedActions: [],
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
            c.id === commentId ? { ...c, resolved: !c.resolved } : c
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
    default:
      return state
  }
}

/**
 * useCommentsChat (FE-2: Comments and Chat)
 *
 * Isolates node comments, active thread state, conversation messages,
 * and proposed actions so FE-2 can be developed independently
 * without causing merge conflicts with canvas or export/history.
 */
export function useCommentsChat({ mapId = null, activeNodeId = null } = {}) {
  const [state, dispatch] = useReducer(commentsChatReducer, {
    ...INITIAL_COMMENTS_CHAT_STATE,
    activeCommentNodeId: activeNodeId,
  })

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
    async (text) => {
      if (!text?.trim()) return null
      dispatch({ type: 'SET_SENDING', payload: true })
      const userMsg = {
        id: `msg-${Date.now()}`,
        role: 'user',
        content: text.trim(),
        created_at: new Date().toISOString(),
        map_id: mapId,
      }
      dispatch({ type: 'ADD_MESSAGE', payload: userMsg })
      dispatch({ type: 'SET_SENDING', payload: false })
      return userMsg
    },
    [mapId],
  )

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
    toggleChat,
    openChatForNode,
    addComment,
    deleteComment,
    resolveComment,
    sendChatMessage,
    applyProposedAction,
  }
}

export default useCommentsChat
