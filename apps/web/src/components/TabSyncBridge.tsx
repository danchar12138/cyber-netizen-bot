import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { subscribeTabSync } from '../tabSync'

export function TabSyncBridge() {
  const queryClient = useQueryClient()

  useEffect(() => subscribeTabSync((message) => {
    void queryClient.invalidateQueries({ queryKey: message.queryKey })
  }), [queryClient])

  return null
}
