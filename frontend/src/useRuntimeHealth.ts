import { useEffect, useRef, type Dispatch, type RefObject, type SetStateAction } from 'react'
import { api } from './api'
import { translate, type Locale } from './i18n'
import { reconcileCurrentEvidence, type SelectionTicket } from './scientificView'
import type { RuntimeDomain, Session } from './types'

type HealthOptions = {
  runtimeReady: boolean
  locale: Locale
  activeStates: ReadonlySet<string>
  selectionRef: RefObject<SelectionTicket>
  sessionRef: RefObject<Session | null>
  sessionStateRef: RefObject<string>
  refreshSession: (id: string, ticket: SelectionTicket) => Promise<Session | null>
  setRuntimeUnavailable: Dispatch<SetStateAction<boolean>>
  setRuntimeDomain: Dispatch<SetStateAction<RuntimeDomain | null>>
  setCriticalError: Dispatch<SetStateAction<string>>
}

// A healthy HTTP response after an outage proves only that the Runtime is
// reachable. Reload its journal and matching projection before restoring any
// previously visible scientific evidence.
export function useRuntimeHealth({ runtimeReady, locale, activeStates, selectionRef,
  sessionRef, sessionStateRef, refreshSession, setRuntimeUnavailable,
  setRuntimeDomain, setCriticalError }: HealthOptions): void {
  const recoveryPending = useRef(false)
  const checkInFlight = useRef(false)
  useEffect(() => {
    if (!runtimeReady) return
    let mounted = true
    const checkHealth = async () => {
      if (checkInFlight.current) return
      checkInFlight.current = true
      try {
        await api.health()
        if (!mounted) return
        if (recoveryPending.current) {
          const ticket = selectionRef.current
          if (ticket.sessionId) {
            const projection = await reconcileCurrentEvidence({
              ticket, selection: () => selectionRef.current,
              refresh: () => refreshSession(ticket.sessionId, ticket),
              projection: () => api.runtimeDomain(ticket.sessionId),
              session: () => sessionRef.current,
            })
            if (!mounted || projection === undefined) return
            setRuntimeDomain(projection)
          }
          recoveryPending.current = false
        }
        setRuntimeUnavailable(false)
      } catch {
        if (!mounted) return
        recoveryPending.current = true
        setRuntimeUnavailable(true)
        setRuntimeDomain(null)
        if (activeStates.has(sessionStateRef.current))
          setCriticalError(translate(locale, 'Scientific Runtime unavailable during RUN. The owned sidecar must be restarted before continuing.'))
      } finally { checkInFlight.current = false }
    }
    const timer = window.setInterval(() => { void checkHealth() }, 5000)
    return () => { mounted = false; window.clearInterval(timer) }
  }, [activeStates, locale, refreshSession, runtimeReady, selectionRef, sessionRef,
    sessionStateRef, setRuntimeUnavailable, setRuntimeDomain, setCriticalError])
}
