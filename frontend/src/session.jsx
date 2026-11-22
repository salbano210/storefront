import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, getAdminPasscode, setAdminPasscode } from './api'

const SessionContext = createContext(null)

const CREATOR_STORAGE = 'storefront.creator_id'

export function readSelectedCreator() {
  try {
    return sessionStorage.getItem(CREATOR_STORAGE) || ''
  } catch {
    return ''
  }
}

export function writeSelectedCreator(creatorId) {
  try {
    if (creatorId) sessionStorage.setItem(CREATOR_STORAGE, creatorId)
    else sessionStorage.removeItem(CREATOR_STORAGE)
  } catch {
    /* ignore */
  }
}

export function SessionProvider({ children }) {
  const [session, setSession] = useState(null)
  const [sessionError, setSessionError] = useState('')
  const [hasPasscode, setHasPasscode] = useState(() => Boolean(getAdminPasscode()))

  const refresh = useCallback(async () => {
    try {
      const data = await api('/api/session')
      setSession(data)
      setSessionError('')
      return data
    } catch (error) {
      setSessionError(error.message)
      return null
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const value = useMemo(
    () => ({
      session,
      sessionError,
      refresh,
      hasPasscode,
      savePasscode(next) {
        setAdminPasscode(next)
        setHasPasscode(Boolean(next))
      },
      clearPasscode() {
        setAdminPasscode('')
        setHasPasscode(false)
      },
    }),
    [session, sessionError, refresh, hasPasscode],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession() {
  const context = useContext(SessionContext)
  if (!context) throw new Error('useSession must be used inside SessionProvider')
  return context
}
