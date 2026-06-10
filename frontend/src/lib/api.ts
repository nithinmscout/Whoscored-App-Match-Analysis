import axios from 'axios'
import {
  ContextualMatchMetricsResponseSchema,
  ExpectedThreatSurfaceResponseSchema,
  MatchAnalysisResponseSchema,
  PitchControlSnapshotResponseSchema,
  SpadlPreviewResponseSchema,
  type ContextualMatchMetricsResponse,
  type ExpectedThreatSurfaceResponse,
  type MatchAnalysisResponse,
  type PitchControlSnapshotResponse,
  type SpadlPreviewResponse,
} from '../schemas/api'
import type { TableRow } from '../types/api'

export const api = axios.create({
  baseURL: 'http://127.0.0.1:8000',
})

export function getApiBaseUrl(): string {
  return String(api.defaults.baseURL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
}

type TablePayload = {
  count: number
  columns: string[]
  rows: TableRow[]
  [key: string]: unknown
}

type StreamEvent = {
  kind?: string
  message?: string
  [key: string]: unknown
}

function parseOrThrow<T>(
  label: string,
  schema: {
    safeParse: (value: unknown) => {
      success: boolean
      data?: T
      error?: { issues: Array<{ path: PropertyKey[]; message: string }> }
    }
  },
  input: unknown,
): T {
  const parsed = schema.safeParse(input)

  if (parsed.success) {
    return parsed.data as T
  }

  const details = parsed.error?.issues
    ?.map((issue) => `${issue.path.join('.') || 'root'}: ${issue.message}`)
    .join(' | ')

  throw new Error(`${label} response validation failed. ${details ?? 'Unknown schema error'}`)
}


export type LeaguePreset = {
  league: string
  nation: string
  tier: string
  folder: string
  group?: string
  season_mode?: string
  has_custom_fallback?: boolean
}

export async function getLeaguePresets(): Promise<LeaguePreset[]> {
  const res = await api.get('/api/loader/league-presets')
  return (res.data?.leagues ?? []) as LeaguePreset[]
}

export async function getScheduleFolders(): Promise<Record<string, string[]>> {
  const res = await api.get('/api/loader/schedule-folders')
  return (res.data?.folders ?? {}) as Record<string, string[]>
}

export async function getScheduleSeasons(nation: string, tier: string): Promise<string[]> {
  const res = await api.get('/api/loader/schedule-seasons', {
    params: { nation, tier },
  })

  return (res.data?.seasons ?? []) as string[]
}

export async function scrapeSchedule(payload: {
  league: string
  season: string
  headless: boolean
  browserpath?: string | null
}): Promise<TablePayload> {
  const res = await api.post('/api/loader/schedule', payload)
  return res.data as TablePayload
}


export function openScheduleStream(params: {
  league: string
  season: string
  headless: boolean
  browserpath?: string
  onEvent: (event: StreamEvent) => void
  onError: (message: string) => void
  onDone?: () => void
}): EventSource {
  const url = new URL(`${getApiBaseUrl()}/api/loader/schedule-stream`)
  url.searchParams.set('league', params.league)
  url.searchParams.set('season', params.season)
  url.searchParams.set('headless', String(params.headless))
  url.searchParams.set('browserpath', params.browserpath ?? '')

  const source = new EventSource(url.toString())
  source.onmessage = (message) => {
    try {
      const parsed = JSON.parse(message.data) as StreamEvent
      params.onEvent(parsed)
      const kind = String(parsed.kind ?? '')
      const stage = String(parsed.stage ?? '')

      if (kind === 'complete' || (kind === 'error' && stage === 'failed')) {
        source.close()
        params.onDone?.()
      }
    } catch (error) {
      params.onError(error instanceof Error ? error.message : 'Could not parse schedule stream event')
    }
  }
  source.onerror = () => {
    params.onError('Schedule stream connection closed or failed.')
    source.close()
    params.onDone?.()
  }
  return source
}

export async function saveSchedule(payload: {
  nation: string
  tier: string
  season: string
  rows: TableRow[]
  league?: string
}): Promise<{
  path: string
  mode: string
  folder?: string
  nation?: string
  tier?: string
  auto_resolved_folder?: boolean
  message: string
}> {
  const res = await api.post('/api/loader/save-schedule', payload)
  return res.data as {
    path: string
    mode: string
    folder?: string
    nation?: string
    tier?: string
    auto_resolved_folder?: boolean
    message: string
  }
}

export async function loadScheduleCsv(payload: {
  nation: string
  tier: string
  season: string
}): Promise<TablePayload> {
  const res = await api.post('/api/loader/load-schedule-csv', payload)
  return res.data as TablePayload
}

export type EventCoverageAudit = {
  kind?: string
  message?: string
  league?: string
  season?: string
  nation?: string
  tier?: string
  folder?: string
  paths?: Record<string, string>
  options?: Record<string, boolean>
  counts?: Record<string, number>
  columns?: string[]
  rows?: TableRow[]
  to_fetch_preview?: TableRow[]
  missing_preview?: TableRow[]
  failed_rows?: TableRow[]
  failed_preview?: TableRow[]
}

export async function getEventCoverage(params: {
  league: string
  season: string
  nation: string
  tier: string
  only_finished: boolean
  overwrite: boolean
  retry_failed: boolean
}): Promise<EventCoverageAudit> {
  const res = await api.get('/api/loader/events-coverage', {
    params: {
      league: params.league,
      season: params.season,
      nation: params.nation,
      tier: params.tier,
      only_finished: params.only_finished,
      overwrite: params.overwrite,
      retry_failed: params.retry_failed,
    },
  })
  return res.data as EventCoverageAudit
}


export type EventCoverageOverviewRow = {
  league: string
  group: string
  nation: string
  tier: string
  folder: string
  season: string
  has_schedule: boolean
  schedule_path: string
  matches_in_schedule: number
  finished_matches: number
  not_completed_matches: number
  with_both_team_events: number
  with_one_team_events: number
  with_no_saved_events: number
  failed_matches: number
  to_fetch_now: number
  coverage_pct: number
  status: string
  priority: number
  message: string
}

export type EventCoverageOverview = {
  kind?: string
  message?: string
  options?: Record<string, string | boolean>
  summary?: Record<string, number>
  columns?: string[]
  rows: EventCoverageOverviewRow[]
}

export async function getEventCoverageOverview(params: {
  season?: string
  only_finished: boolean
  overwrite: boolean
  retry_failed: boolean
}): Promise<EventCoverageOverview> {
  const res = await api.get('/api/loader/events-coverage-overview', {
    params: {
      season: params.season ?? '',
      only_finished: params.only_finished,
      overwrite: params.overwrite,
      retry_failed: params.retry_failed,
    },
  })
  return res.data as EventCoverageOverview
}

export function openFetchEventsStream(params: {
  league: string
  season: string
  nation: string
  tier: string
  headless: boolean
  browserpath?: string
  only_finished: boolean
  overwrite: boolean
  retry_failed: boolean
  fail_fast: boolean
  scrape_positions: boolean
  onEvent: (event: StreamEvent) => void
  onError: (message: string) => void
  onDone?: () => void
}): EventSource {
  const url = new URL(`${getApiBaseUrl()}/api/loader/fetch-events-stream`)
  url.searchParams.set('league', params.league)
  url.searchParams.set('season', params.season)
  url.searchParams.set('nation', params.nation)
  url.searchParams.set('tier', params.tier)
  url.searchParams.set('headless', String(params.headless))
  url.searchParams.set('browserpath', params.browserpath ?? '')
  url.searchParams.set('only_finished', String(params.only_finished))
  url.searchParams.set('overwrite', String(params.overwrite))
  url.searchParams.set('retry_failed', String(params.retry_failed))
  url.searchParams.set('fail_fast', String(params.fail_fast))
  url.searchParams.set('scrape_positions', String(params.scrape_positions))

  const source = new EventSource(url.toString())
  source.onmessage = (message) => {
    try {
      const parsed = JSON.parse(message.data) as StreamEvent
      params.onEvent(parsed)
      if (['complete', 'stopped'].includes(String(parsed.kind ?? ''))) {
        source.close()
        params.onDone?.()
      }
    } catch (error) {
      params.onError(error instanceof Error ? error.message : 'Could not parse stream event')
    }
  }
  source.onerror = () => {
    params.onError('Event stream connection closed or failed.')
    source.close()
    params.onDone?.()
  }
  return source
}

export async function getProcessedStoreStatus(params: {
  nation: string
  tier: string
  season: string
}): Promise<Record<string, unknown>> {
  const res = await api.get('/api/processed/status', { params })
  return res.data as Record<string, unknown>
}

export async function rebuildProcessedStore(params: {
  nation: string
  tier: string
  season: string
  force?: boolean
}): Promise<Record<string, unknown>> {
  const res = await api.post('/api/processed/rebuild', null, {
    params: {
      nation: params.nation,
      tier: params.tier,
      season: params.season,
      force: params.force ?? false,
    },
  })
  return res.data as Record<string, unknown>
}


export function openProcessedStoreStream(params: {
  nation: string
  tier: string
  season: string
  force?: boolean
  onEvent: (event: StreamEvent) => void
  onError: (message: string) => void
  onDone?: () => void
}): EventSource {
  const url = new URL(`${getApiBaseUrl()}/api/processed/rebuild-stream`)
  url.searchParams.set('nation', params.nation)
  url.searchParams.set('tier', params.tier)
  url.searchParams.set('season', params.season)
  url.searchParams.set('force', String(params.force ?? false))

  const source = new EventSource(url.toString())
  source.onmessage = (message) => {
    try {
      const parsed = JSON.parse(message.data) as StreamEvent
      params.onEvent(parsed)
      const kind = String(parsed.kind ?? '')
      const stage = String(parsed.stage ?? '')

      if (kind === 'complete' || (kind === 'error' && stage === 'failed')) {
        source.close()
        params.onDone?.()
      }
    } catch (error) {
      params.onError(error instanceof Error ? error.message : 'Could not parse processed store stream event')
    }
  }
  source.onerror = () => {
    params.onError('Processed store stream connection closed or failed.')
    source.close()
    params.onDone?.()
  }
  return source
}

export async function getMatchAnalysis(params: {
  nation: string
  tier: string
  season: string
  match_id?: number | null
  matchId?: number | null
  game_state?: string
  perspective?: string
}): Promise<MatchAnalysisResponse> {
  const res = await api.get('/api/analysis/match-analysis', {
    params: {
      nation: params.nation,
      tier: params.tier,
      season: params.season,
      match_id: params.match_id ?? params.matchId ?? undefined,
      game_state: params.game_state ?? undefined,
      perspective: params.perspective ?? undefined,
    },
  })

  return parseOrThrow('Match analysis', MatchAnalysisResponseSchema, res.data)
}

export async function getViewerMatchEvents(params: {
  nation: string
  tier: string
  season: string
  match_id: number
  limit?: number
}): Promise<TablePayload> {
  const res = await api.get('/api/viewer/match-events', { params })
  return res.data as TablePayload
}


export type TeamDashboardMetric = string | number | boolean | null

export type TeamOverview = {
  matches_covered?: number
  events_covered?: number
  match_ids_covered?: number[]
  goals?: number
  goals_against?: number | null
  shots?: number
  shots_against?: number | null
  shots_on_target?: number | null
  shots_on_target_against?: number | null
  box_entries?: number
  box_entries_against?: number | null
  final_third_entries?: number
  final_third_entries_against?: number | null
  possession_proxy?: number | null
  field_tilt_proxy?: number | null
  defensive_actions?: number
  high_regains?: number | null
  set_piece_threat?: number | null
  set_piece_shots_against?: number | null
  xg_for?: number | null
  non_penalty_xg_for?: number | null
  xg_against?: number | null
  non_penalty_xg_against?: number | null
  xg_per_shot?: number | null
  xg_conceded?: number | null
  xg_conceded_per_shot?: number | null
  xg_overperformance?: number | null
  xg_underperformance?: number | null
  xa?: number | null
  open_play_xa?: number | null
  set_piece_xa?: number | null
  xt?: number | null
  open_play_xt?: number | null
  set_piece_xt?: number | null
  xt_conceded_proxy?: number | null
}

export type TeamPhaseBucket = {
  key?: string
  title?: string
  value?: number
  metric?: string
  note?: string
}

export type TeamLaneSummary = {
  lane?: string
  label?: string
  count?: number
  share_pct?: number
  final_third_entries?: number
  box_entries?: number
  shots?: number
  value?: number
  y_min?: number
  y_max?: number
}

export type TeamPlayerContribution = {
  player?: string
  events?: number
  shots?: number
  goals?: number
  passes?: number
  carries?: number
  crosses?: number
  final_third_entries?: number
  box_entries?: number
  defensive_actions?: number
  high_regains?: number
  set_piece_involvement?: number
  corner_involvement?: number
  free_kick_involvement?: number
  throw_in_involvement?: number
  progressive_actions?: number
  xg?: number
  np_xg?: number
  xa?: number
  open_play_xa?: number
  set_piece_xa?: number
  xt?: number
  open_play_xt?: number
  set_piece_xt?: number
  xt_by_pass?: number
  xt_by_cross?: number
  xt_by_carry?: number
  ranking_within_team?: number
  involvement_score?: number
}

export type TeamMatchLogRow = {
  match_id?: number
  date?: string
  opponent?: string
  home_away?: string
  score?: string
  result?: string
  events?: number
  shots?: number
  box_entries?: number
  final_third_entries?: number
  defensive_actions?: number
  set_piece_events?: number
}


export type TeamMetricRadarRow = {
  key: string
  label: string
  value?: number | null
  per_match?: number | null
  rank?: number
  rank_text?: string
  teams_compared?: number
  percentile?: number
  league_min?: number
  league_max?: number
  league_average?: number
  higher_is_better?: boolean
  category?: string
}

export type TeamLeagueContext = {
  available?: boolean
  teams_compared?: number
  selected_team?: string
  comparison_scope?: string
  note?: string
}

export type TeamCommonLineupPlayer = {
  player?: string
  player_id?: string | number | null
  position_label?: string | null
  role_group?: string
  shirt_no?: string | number | null
  appearances_covered?: number
  starts_estimated?: number | null
  events?: number
  minutes_proxy?: number | null
  avg_x?: number
  avg_y?: number
  pitch_x?: number
  pitch_y?: number
  confidence?: string
}

export type TeamCommonLineup = {
  formation_guess?: string
  confidence?: string
  method?: string
  note?: string
  players?: TeamCommonLineupPlayer[]
}

export type TeamPitchPoint = Record<string, TeamDashboardMetric | undefined>

export type TeamHeatmap = {
  x_bins?: number
  y_bins?: number
  cells?: Array<Record<string, TeamDashboardMetric>>
}


export type TeamPhaseRadarMetric = {
  label?: string
  value?: TeamDashboardMetric
  score?: number
  percentile?: number
  strength?: string
  note?: string
}

export type TeamPhaseRadarGroup = {
  key: string
  title: string
  score?: number
  strength?: string
  metrics?: TeamPhaseRadarMetric[]
}

export type TeamPhaseKpiBreakdown = {
  key?: string
  title?: string
  score?: number
  strength?: string
  items?: TeamPhaseRadarMetric[]
}

export type TeamShapeProfile = TeamCommonLineup & {
  mode?: string
  title?: string
}

export type TeamActionMapGroup = Record<string, TeamPitchPoint[] | undefined>

export type TeamLaneKpiGroup = Record<string, TeamLaneSummary[] | undefined>

export type TeamShotMaps = {
  points?: TeamPitchPoint[]
  shot_map?: TeamPitchPoint[]
  shot_heatmap?: TeamHeatmap
  xg_map?: { points?: TeamPitchPoint[]; heatmap?: TeamHeatmap }
}

export type TeamPlayerInfluenceCategory = {
  key?: string
  title?: string
  metric?: keyof TeamPlayerContribution | string
  why?: string
  players?: TeamPlayerContribution[]
}

export type TeamPlayerInfluenceDashboard = {
  categories?: TeamPlayerInfluenceCategory[]
}

export type TeamDangerConceded = {
  available?: boolean
  shots_conceded?: number | null
  shots_on_target_conceded?: number | null
  goals_conceded?: number | null
  shot_locations?: TeamPitchPoint[]
  goalmouth_locations?: TeamPitchPoint[]
  box_shots_conceded?: number | null
  set_piece_shots_conceded?: number | null
  open_play_shots_conceded?: number | null
  xg_conceded?: number | null
  note?: string
}

export type TeamAttackingProfile = {
  shot_locations?: TeamPitchPoint[]
  goalmouth_locations?: TeamPitchPoint[]
  xg_shot_quality_map?: TeamPitchPoint[]
  final_third_pass_locations?: TeamPitchPoint[]
  final_third_entries_by_lane?: TeamLaneSummary[]
  box_entries_by_lane?: TeamLaneSummary[]
  box_entry_locations?: TeamPitchPoint[]
  chance_creation_locations?: TeamPitchPoint[]
  xa_chance_creation_map?: TeamPitchPoint[]
  xt_action_map?: TeamPitchPoint[]
  xt_heatmap?: TeamHeatmap
  xt_progression_lanes?: TeamLaneSummary[]
  top_xt_actions?: TeamPitchPoint[]
  progression_lane_map?: TeamLaneSummary[]
  crossing_profile?: { crosses?: number; wide_deliveries?: number; locations?: TeamPitchPoint[]; by_lane?: TeamLaneSummary[] }
  xg?: number
  non_penalty_xg?: number
  xa?: number
  open_play_xa?: number
  set_piece_xa?: number
  xt?: number
  open_play_xt?: number
  set_piece_xt?: number
  top_players?: TeamPlayerContribution[]
  top_creators?: TeamPlayerContribution[]
  top_xt_players?: TeamPlayerContribution[]
  territory_heatmap?: TeamHeatmap
  summary_text?: string
}

export type TeamDefensiveProfile = {
  defensive_action_locations?: TeamPitchPoint[]
  defensive_height?: number | null
  central_protection?: number
  central_protection_locations?: TeamPitchPoint[]
  central_protection_summary?: string
  wide_forcing?: number
  wide_forcing_locations?: TeamPitchPoint[]
  wide_forcing_summary?: string
  shots_conceded_locations?: TeamPitchPoint[]
  xg_conceded_shot_quality_map?: TeamPitchPoint[]
  opponent_xt_threat_map?: TeamPitchPoint[]
  high_regain_locations?: TeamPitchPoint[]
  box_entries_conceded?: number | null
  box_entries_conceded_by_lane?: TeamLaneSummary[]
  final_third_entries_conceded?: number | null
  final_third_entries_conceded_by_lane?: TeamLaneSummary[]
  danger_conceded_heatmap?: TeamHeatmap
  top_players?: TeamPlayerContribution[]
  danger_conceded?: TeamDangerConceded
  interpretation_note?: string
}

export type TeamTransitionProfile = {
  high_regains?: number
  regain_locations?: TeamPitchPoint[]
  regain_to_attack_sequences?: number
  regain_to_shot_sequences?: number
  regain_to_box_entry_sequences?: number
  fast_attacks_after_regains?: number
  opponent_high_regains?: number | null
  opponent_high_regain_locations?: TeamPitchPoint[]
  opponent_transition_threat?: number | null
  opponent_transition_heatmap?: TeamHeatmap
  opponent_xt_threat_map?: TeamPitchPoint[]
  top_players?: TeamPlayerContribution[]
  note?: string
}


export type TeamSetPieceSectionSide = {
  events?: number | null
  delivery_zones?: TeamLaneSummary[]
  delivery_locations?: TeamPitchPoint[]
  delivery_lines?: TeamPitchPoint[]
  shot_ending_deliveries?: TeamPitchPoint[]
  high_threat_deliveries?: TeamPitchPoint[]
  dangerous_zones?: TeamLaneSummary[]
  zones?: TeamLaneSummary[]
  shots?: number | null
  shot_locations?: TeamPitchPoint[]
  xg?: number | null
  main_takers?: TeamPlayerContribution[]
  main_targets?: TeamPlayerContribution[]
}

export type TeamSetPieceSection = {
  name?: string
  "for"?: TeamSetPieceSectionSide
  against?: TeamSetPieceSectionSide
}

export type TeamSetPieceProfile = {
  overview?: {
    set_piece_volume?: number
    set_piece_shot_creation?: number
    set_piece_xg?: number | null
    set_piece_shots_conceded?: number | null
    set_piece_xg_conceded?: number | null
    best_takers?: TeamPlayerContribution[]
    most_targeted_players?: TeamPlayerContribution[]
  }
  throw_ins?: TeamSetPieceSection
  corners?: TeamSetPieceSection
  free_kicks?: TeamSetPieceSection
  corners_for?: number
  corners_against?: number | null
  free_kicks_for?: number
  free_kicks_against?: number | null
  throw_ins_for?: number
  throw_ins_against?: number | null
  set_piece_shots?: number
  set_piece_shots_against?: number | null
  delivery_zones?: TeamLaneSummary[]
  delivery_locations?: TeamPitchPoint[]
  delivery_zones_against?: TeamLaneSummary[]
  delivery_locations_against?: TeamPitchPoint[]
  main_takers?: TeamPlayerContribution[]
  main_targets?: TeamPlayerContribution[]
  defensive_set_piece_events?: number | null
  defensive_set_piece_shots?: number | null
  defensive_set_piece_shot_locations?: TeamPitchPoint[]
}

export type TeamProfileBlock = {
  team?: string
  nation?: string
  tier?: string
  season?: string
  matches_covered?: number
  event_rows?: number
  opponent_rows?: number
  style_tags?: string[]
  summary_text?: string
}

export type TeamSeasonProfile = {
  overview?: TeamOverview
  phase_buckets?: TeamPhaseBucket[]
  match_event_counts?: Record<string, number>
  context?: {
    team_events?: number
    opponent_events?: number
    all_selected_matches_events?: number
    match_ids?: number[]
    source_modes?: string[]
  }
}

export type TeamSeasonComparisonRow = {
  season?: string
  matches_covered?: number
  goals_for?: number | null
  goals_against?: number | null
  shots_for?: number | null
  shots_against?: number | null
  box_entries_for?: number | null
  box_entries_against?: number | null
  final_third_entries_for?: number | null
  final_third_entries_against?: number | null
  set_piece_shots_for?: number | null
  set_piece_shots_against?: number | null
  high_regains?: number | null
  defensive_actions?: number | null
  xg_for?: number | null
  xg_against?: number | null
  xa?: number | null
  xt?: number | null
  opponent_rows_available?: boolean
  data_quality_status?: string
  source_path?: string
}

export type TeamMultiSeasonProfile = {
  available?: boolean
  rows?: TeamSeasonComparisonRow[]
  note?: string
}

export type TeamPlayerProfile = {
  players?: TeamPlayerContribution[]
  top_players?: TeamPlayerContribution[]
  top_attacking_players?: TeamPlayerContribution[]
  top_defensive_players?: TeamPlayerContribution[]
  top_transition_players?: TeamPlayerContribution[]
  top_set_piece_players?: TeamPlayerContribution[]
}

export type TeamDataQuality = {
  load_mode?: string
  source_path?: string
  source_rows?: number
  own_team_rows?: number
  team_rows?: number
  opponent_rows?: number
  match_ids_covered?: number[]
  matches_with_opponent_rows?: number[]
  matches_without_opponent_rows?: number[]
  source_files_used?: string[]
  opponent_rows_available?: boolean
  league_rows_used_for_radar?: number
  league_teams_compared?: number
  matches_covered?: number
  xg_model_status?: Record<string, unknown>
  xa_model_status?: Record<string, unknown>
  xt_model_status?: Record<string, unknown>
  notes?: string[]
}

export type TeamSummaryResponse = {
  team: string
  nation: string
  tier: string
  season: string
  path?: string
  rows: number
  matches: number
  columns: string[]
  type_counts?: Array<{ type: string; count: number }>
  player_counts?: Array<{ player: string; events: number }>
  phase_buckets?: TeamPhaseBucket[]
  top_players?: TeamPlayerContribution[]
  overview?: TeamOverview
  style_tags?: string[]
  match_log?: TeamMatchLogRow[]
  profile?: TeamProfileBlock
  season_profile?: TeamSeasonProfile
  league_context?: TeamLeagueContext
  metric_radar?: TeamMetricRadarRow[]
  phase_radar_groups?: TeamPhaseRadarGroup[]
  phase_kpi_breakdowns?: Record<string, TeamPhaseKpiBreakdown>
  common_lineup?: TeamCommonLineup
  in_possession_shape?: TeamShapeProfile
  defensive_shape?: TeamShapeProfile
  attacking_territory?: Record<string, TeamHeatmap | undefined>
  shot_maps?: TeamShotMaps
  shot_heatmap?: TeamHeatmap
  xg_map?: { points?: TeamPitchPoint[]; heatmap?: TeamHeatmap }
  pass_maps?: TeamActionMapGroup
  carry_maps?: TeamActionMapGroup
  lane_kpis?: TeamLaneKpiGroup
  seasonal_defensive_dashboard?: Record<string, unknown>
  set_piece_delivery_maps?: Record<string, TeamSetPieceSection | undefined>
  player_influence_dashboard?: TeamPlayerInfluenceDashboard
  multi_season_profile?: TeamMultiSeasonProfile
  attacking_profile?: TeamAttackingProfile
  defensive_profile?: TeamDefensiveProfile
  transition_profile?: TeamTransitionProfile
  set_piece_profile?: TeamSetPieceProfile
  player_profile?: TeamPlayerProfile
  attacking?: TeamAttackingProfile
  defensive?: TeamDefensiveProfile
  transitions?: TeamTransitionProfile
  set_pieces?: TeamSetPieceProfile
  players?: TeamPlayerContribution[]
  data_quality?: TeamDataQuality
  render_meta?: {
    generated_at?: number
    raw_rows_loaded_by_default?: boolean
    raw_preview_default_limit?: number
    started_at?: string
    completed_at?: string
    duration_ms?: number
    cache_hit?: boolean
    memory_cache_hit?: boolean
    parquet_profile_hit?: boolean
    parquet_rebuilt?: boolean
    load_mode?: string
    cache_version?: string
    profile_store_path?: string
    club_profiles_rows?: number
    source_fingerprint?: string
    phases?: Array<string | { label?: string; weight?: number; [key: string]: unknown }>
    data_source_counts?: Record<string, unknown>
    message?: string
  }
}

export type CorrelationMethod = 'pearson' | 'spearman' | 'kendall'

export type LeagueMetricCatalogItem = {
  key: string
  label: string
  phase?: string
  description?: string
  enabled?: boolean
}

export type LeagueStyleDimension = {
  key: string
  label: string
  metrics?: string[]
  league_average?: number
  top_teams?: Array<{ team: string; score: number }>
}

export type LeagueTeamStyleRow = Record<string, TeamDashboardMetric | string[] | number[] | undefined> & {
  team: string
  matches?: number
  rows?: number
  match_ids?: number[]
  style_tags?: string[]
  overall_style_score?: number
}

export type LeagueCorrelationPair = {
  x: string
  y: string
  x_label?: string
  y_label?: string
  value: number
  strength?: string
}

export type LeagueCorrelationPayload = {
  available?: boolean
  method?: CorrelationMethod | string
  metrics?: LeagueMetricCatalogItem[]
  matrix?: LeagueCorrelationPair[]
  strongest_positive?: LeagueCorrelationPair[]
  strongest_negative?: LeagueCorrelationPair[]
  note?: string
}

export type LeaguePcaPayload = {
  available?: boolean
  explained_variance_pct?: number[]
  team_scores?: Array<{ team: string; pc1: number; pc2: number }>
  loadings?: Array<{ metric: string; label?: string; pc1: number; pc2: number }>
  note?: string
}

export type LeagueClusterPayload = {
  available?: boolean
  method?: string
  cluster_count?: number
  metrics?: string[]
  clusters?: Array<{ cluster: number; label: string; teams: string[]; centroid?: Record<string, number> }>
  note?: string
}

export type LeagueOutlierRow = {
  team: string
  metric: string
  label?: string
  value?: number
  z_score?: number
  league_average?: number
  direction?: string
}

export type LeagueAnalysisResponse = {
  nation: string
  tier: string
  season: string
  overview?: {
    teams_compared?: number
    event_rows?: number
    schedule_matches?: number
    event_matches?: number
    correlation_method?: string
    min_matches?: number
    dimension_scores?: LeagueStyleDimension[]
    dominant_dimensions?: LeagueStyleDimension[]
  }
  metric_catalog?: LeagueMetricCatalogItem[]
  style_dimensions?: LeagueStyleDimension[]
  teams?: LeagueTeamStyleRow[]
  correlations?: LeagueCorrelationPayload
  pca?: LeaguePcaPayload
  clusters?: LeagueClusterPayload
  outliers?: LeagueOutlierRow[]
  findings?: string[]
  data_quality?: {
    schedule?: Record<string, unknown>
    source_files?: string[]
    source_file_count?: number
    model_quality?: Record<string, unknown>
    notes?: string[]
  }
  render_meta?: {
    generated_at?: number
    duration_ms?: number
    cache_hit?: boolean
    model_version?: string
    started_at?: string
    completed_at?: string
    phases?: Array<string | { label?: string; weight?: number; [key: string]: unknown }>
    data_source_counts?: Record<string, unknown>
    message?: string
  }
}

export async function getLeagueAnalysis(params: {
  nation: string
  tier: string
  season: string
  method?: CorrelationMethod | string
  min_matches?: number
}): Promise<LeagueAnalysisResponse> {
  const res = await api.get('/api/viewer/league-analysis', {
    params: {
      nation: params.nation,
      tier: params.tier,
      season: params.season,
      method: params.method ?? 'pearson',
      min_matches: params.min_matches ?? 1,
    },
  })
  return res.data as LeagueAnalysisResponse
}


export async function getSavedTeams(params: {
  nation: string
  tier: string
  season: string
}): Promise<{ teams: Array<{ team: string; path: string }>; root: string }> {
  const res = await api.get('/api/viewer/teams', { params })
  return res.data as { teams: Array<{ team: string; path: string }>; root: string }
}

export async function getTeamEvents(params: {
  nation: string
  tier: string
  season: string
  team: string
  match_id?: number | null
  event_type?: string | null
  player?: string | null
  limit?: number
}): Promise<TablePayload> {
  const res = await api.get('/api/viewer/team-events', {
    params: {
      nation: params.nation,
      tier: params.tier,
      season: params.season,
      team: params.team,
      match_id: params.match_id ?? undefined,
      event_type: params.event_type || undefined,
      player: params.player || undefined,
      limit: params.limit ?? 500,
    },
  })
  return res.data as TablePayload
}

export async function getTeamSummary(params: {
  nation: string
  tier: string
  season: string
  team: string
}): Promise<TeamSummaryResponse> {
  const res = await api.get('/api/viewer/team-summary', { params })
  return res.data as TeamSummaryResponse
}

export async function rebuildTeamAnalysisProfileStore(params: {
  nation: string
  tier: string
  season: string
  force?: boolean
}): Promise<Record<string, unknown>> {
  const res = await api.post('/api/viewer/team-analysis-cache/rebuild', null, {
    params: {
      nation: params.nation,
      tier: params.tier,
      season: params.season,
      force: params.force ?? true,
    },
  })
  return res.data as Record<string, unknown>
}

export async function getDebugHealth(): Promise<Record<string, unknown>> {
  const res = await api.get('/api/debug/health')
  return res.data as Record<string, unknown>
}

export async function getDebugEnvironment(): Promise<Record<string, unknown>> {
  const res = await api.get('/api/debug/environment')
  return res.data as Record<string, unknown>
}

export async function getMatchExpectedThreat(params: {
  nation: string
  tier: string
  season: string
  match_id: number
}): Promise<ExpectedThreatSurfaceResponse> {
  const res = await api.get('/api/spatial/match-xt', { params })
  return parseOrThrow('Expected threat surface', ExpectedThreatSurfaceResponseSchema, res.data)
}

export async function getExpectedThreatSurface(params: {
  nation: string
  tier: string
  season: string
  match_id: number
}): Promise<ExpectedThreatSurfaceResponse> {
  return getMatchExpectedThreat(params)
}

export async function getMatchContextualMetrics(params: {
  nation: string
  tier: string
  season: string
  match_id: number
}): Promise<ContextualMatchMetricsResponse> {
  const res = await api.get('/api/spatial/match-context', { params })
  return parseOrThrow('Contextual match metrics', ContextualMatchMetricsResponseSchema, res.data)
}

export async function getContextualMatchMetrics(params: {
  nation: string
  tier: string
  season: string
  match_id: number
}): Promise<ContextualMatchMetricsResponse> {
  return getMatchContextualMetrics(params)
}

export async function getPitchControlSnapshot(params: {
  nation: string
  tier: string
  season: string
  match_id: number
  minute?: number | null
}): Promise<PitchControlSnapshotResponse> {
  const res = await api.get('/api/spatial/pitch-control-snapshot', { params })
  return parseOrThrow('Pitch control snapshot', PitchControlSnapshotResponseSchema, res.data)
}

export async function getSpadlPreview(params: {
  nation: string
  tier: string
  season: string
  match_id: number
  limit?: number
}): Promise<SpadlPreviewResponse> {
  const res = await api.get('/api/spatial/match-spadl', { params })
  return parseOrThrow('SPADL preview', SpadlPreviewResponseSchema, res.data)
}

export async function getDebugMatchAnalysis(params: {
  nation: string
  tier: string
  season: string
  match_id: number | string
  game_state?: string
  perspective?: string
}): Promise<Record<string, unknown>> {
  const res = await api.get('/api/debug/match-analysis', {
    params: {
      nation: params.nation,
      tier: params.tier,
      season: params.season,
      match_id: params.match_id,
      game_state: params.game_state ?? 'all',
      perspective: params.perspective ?? 'home',
    },
  })
  return res.data as Record<string, unknown>
}