import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 3,
      retryDelay: (attemptIndex) => {
        const delay = Math.min(1000 * 2 ** attemptIndex, 30_000)
        return delay
      },
      staleTime: 1000 * 60 * 30,
      gcTime: 1000 * 60 * 60 * 6,
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
    },
  },
})