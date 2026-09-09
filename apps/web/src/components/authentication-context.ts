import { createContext, useContext } from 'react'

import type { AuthenticationConfig } from '../api'

export interface AuthenticationContextValue {
  mode: AuthenticationConfig['mode']
  signOut: () => Promise<void>
}

export const AuthenticationContext = createContext<AuthenticationContextValue>({
  mode: 'development',
  signOut: async () => {},
})

export function useAuthentication() {
  return useContext(AuthenticationContext)
}
