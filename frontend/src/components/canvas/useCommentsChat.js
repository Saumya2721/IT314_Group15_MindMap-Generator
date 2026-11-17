import { useReducer, useCallback, useRef, useEffect } from 'react'
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
    case 'SET_NODE_COMMENTS': {
      const { nodeId, comments } = action.payload
      return {
        ...state,
        commentsByNodeId: {
          ...state.commentsByNodeId,
          [nodeId]: comments,
        },
      }
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
    case 'REPLACE_COMMENT': {
      const { nodeId, tempId, newComment } = action.payload
      return {
        ...state,
        commentsByNodeId: {
          ...state.commentsByNodeId,
          [nodeId]: (state.commentsByNodeId[nodeId] || []).map((c) =>
            c.id === tempId ? newComment : c,
          ),
        },
      }
    }
    case 'REMOVE_TEMP_COMMENT': {
      const { nodeId, tempId } = action.payload
      return {
        ...state,
        commentsByNodeId: {
          ...state.commentsByNodeId,
          [nodeId]: (state.commentsByNodeId[nodeId] || []).filter((c) => c.id !== tempId),
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
    case 'ADD_MESSAGE':
      return { ...state, messages: [...state.messages, action.payload] }
    case 'UPDATE_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.payload.id ? { ...m, ...action.payload } : m,
        ),
      }
    case 'UPDATE_MESSAGE_CONFIRMATION': {
      const { messageId, isConfirmed, metadata } = action.payload
      return {
        ...state,
        messages: state.messages.map((m) => {
          if (m.id !== messageId) return m
          let currentMeta = m.metadata || {}
          if (typeof currentMeta === 'string') {
            try {
              currentMeta = JSON.parse(currentMeta)
            } catch {
              currentMeta = {}
            }
          }
          const newMeta = {
            ...currentMeta,
            ...(metadata || {}),
            execution_status:
              metadata?.execution_status ||
              (isConfirmed === false
                ? 'dismissed'
                : isConfirmed === true
                  ? 'applied'
                  : currentMeta.execution_status),
          }
          return {
            ...m,
            is_confirmed: isConfirmed,
            metadata: newMeta,
          }
        }),
      }
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

  const loadCommentsForNode = useCallback(
    async (nodeId) => {
      if (!mapId || !nodeId) return
      try {
        const backendComments = await mindMapService.getNodeComments(mapId, nodeId)
        const mapped = (backendComments || []).map((c) => ({
          id: c.id,
          nodeId: c.node_id,
          text: c.content,
          resolved: c.resolved !== undefined ? Boolean(c.resolved) : c.status === 'resolved',
          author:
            c.author_name || (c.user_id ? `User ${String(c.user_id).slice(0, 6)}` : 'Collaborator'),
          createdAt: c.created_at,
        }))
        dispatch({ type: 'SET_NODE_COMMENTS', payload: { nodeId, comments: mapped } })
      } catch (err) {
        console.warn('Failed to fetch node comments:', err)
      }
    },
    [mapId],
  )

  useEffect(() => {
    if (activeNodeId) {
      loadCommentsForNode(activeNodeId)
    }
  }, [activeNodeId, loadCommentsForNode])

  const addComment = useCallback(
    async (nodeId, commentText, author = 'You') => {
      if (!nodeId || !commentText?.trim()) return null
      const tempId = `temp-${Date.now()}`
      const comment = {
        id: tempId,
        nodeId,
        text: commentText.trim(),
        resolved: false,
        author,
        createdAt: new Date().toISOString(),
      }
      dispatch({ type: 'ADD_COMMENT', payload: { nodeId, comment } })

      if (mapId) {
        try {
          const created = await mindMapService.createNodeComment(mapId, nodeId, commentText.trim())
          if (created?.id) {
            const isResolved =
              created.resolved !== undefined
                ? Boolean(created.resolved)
                : created.status === 'resolved'
            dispatch({
              type: 'REPLACE_COMMENT',
              payload: {
                nodeId,
                tempId,
                newComment: {
                  id: created.id,
                  nodeId: created.node_id,
                  text: created.content,
                  resolved: isResolved,
                  author: created.author_name || author,
                  createdAt: created.created_at,
                },
              },
            })
          }
        } catch (err) {
          console.error('Failed to save comment to backend:', err)
          // Rollback optimistic addition on failure without stale closure
          dispatch({
            type: 'REMOVE_TEMP_COMMENT',
            payload: { nodeId, tempId },
          })
        }
      }
      return comment
    },
    [mapId],
  )

  const deleteComment = useCallback(
    async (nodeId, commentId) => {
      const prevComments = state.commentsByNodeId[nodeId] || []
      dispatch({ type: 'DELETE_COMMENT', payload: { nodeId, commentId } })
      if (mapId && !String(commentId).startsWith('temp-')) {
        try {
          await mindMapService.deleteNodeComment(mapId, nodeId, commentId)
        } catch (err) {
          console.error('Failed to delete comment on backend:', err)
          // Rollback optimistic deletion on failure
          dispatch({
            type: 'SET_NODE_COMMENTS',
            payload: { nodeId, comments: prevComments },
          })
        }
      }
    },
    [mapId, state.commentsByNodeId],
  )

  const resolveComment = useCallback(
    async (nodeId, commentId) => {
      const prevComments = state.commentsByNodeId[nodeId] || []
      const current = prevComments.find((c) => c.id === commentId)
      const nextResolved = !current?.resolved
      dispatch({ type: 'TOGGLE_RESOLVE_COMMENT', payload: { nodeId, commentId } })
      if (mapId && !String(commentId).startsWith('temp-')) {
        try {
          await mindMapService.updateNodeComment(mapId, nodeId, commentId, {
            status: nextResolved ? 'resolved' : 'open',
            resolved: nextResolved,
          })
        } catch (err) {
          console.error('Failed to toggle comment resolved on backend:', err)
          // Rollback optimistic resolve toggle on failure
          dispatch({
            type: 'SET_NODE_COMMENTS',
            payload: { nodeId, comments: prevComments },
          })
        }
      }
    },
    [mapId, state.commentsByNodeId],
  )

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
        const targetNodeId = contextNode?.id || null
        const res = await mindMapService.sendChatMessage(mapId, userMsg.content, null, targetNodeId)
        if (res?.job_id) {
          // Poll the async generation job until completed or failed
          let attempts = 0
          const maxAttempts = 60
          let completed = false

          while (attempts < maxAttempts && !completed) {
            await new Promise((r) => setTimeout(r, 1000))
            attempts++
            const jobStatus = await mindMapService.pollJobStatus(mapId, res.job_id)

            if (jobStatus) {
              if (jobStatus.status === 'completed') {
                completed = true
                await loadChatHistory(mapId)
                lastFailedRef.current = null
                return
              } else if (jobStatus.status === 'failed' || jobStatus.status === 'rejected') {
                throw new Error(jobStatus.error_message || 'Assistant failed to generate response.')
              }
            }
          }

          if (!completed) {
            throw new Error('Chat response timed out. Please try again.')
          }
        } else if (res?.chat_turn && res.chat_turn.role === 'assistant') {
          dispatch({ type: 'ADD_MESSAGE', payload: res.chat_turn })
          lastFailedRef.current = null
        } else {
          // If no background job triggered, reload transcript to sync state
          await loadChatHistory(mapId)
          lastFailedRef.current = null
        }
      } catch (err) {
        dispatch({
          type: 'SET_ERROR',
          payload: err.message || 'Failed to send message. Please retry.',
        })
      } finally {
        dispatch({ type: 'SET_SENDING', payload: false })
      }
    },
    [mapId, loadChatHistory],
  )

  const retryLastMessage = useCallback(() => {
    if (!lastFailedRef.current) return
    const { text, contextNode } = lastFailedRef.current
    sendChatMessage(text, contextNode)
  }, [sendChatMessage])

  const confirmAction = useCallback(async (messageId, proposedAction) => {
    try {
      const resp = await mindMapService.confirmProposedAction(messageId, true)
      dispatch({
        type: 'UPDATE_MESSAGE_CONFIRMATION',
        payload: {
          messageId,
          isConfirmed: true,
          metadata: resp?.updated_message?.metadata || { execution_status: 'applied' },
        },
      })
      return resp || proposedAction
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
      const resp = await mindMapService.confirmProposedAction(messageId, false)
      dispatch({
        type: 'UPDATE_MESSAGE_CONFIRMATION',
        payload: {
          messageId,
          isConfirmed: false,
          metadata: resp?.updated_message?.metadata || { execution_status: 'dismissed' },
        },
      })
      return resp
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
    dispatch,
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
    loadCommentsForNode,
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
