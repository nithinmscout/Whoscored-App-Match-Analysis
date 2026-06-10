import type { MatchCentreData } from './schemas/matchCenter'

declare global {
  interface Window {
    matchCentreData?: MatchCentreData
  }
}

export {}
