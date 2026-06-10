import { z } from 'zod'

const NumberOrNull = z.union([z.number(), z.null()])
const UnknownRecord = z.record(z.string(), z.unknown())

export const TableRowSchema = z.record(
  z.string(),
  z.union([z.string(), z.number(), z.boolean(), z.null()]),
)

export const TableResponseSchema = z.object({
  count: z.number(),
  columns: z.array(z.string()),
  rows: z.array(TableRowSchema),
})

export const BuildProgressEventSchema = z.object({
  kind: z.string(),
  step: z.string().optional(),
  message: z.string().optional(),
  percent: z.number().optional(),
  elapsed_seconds: z.number().optional(),
  eta_seconds: z.number().nullable().optional(),
  completed_files: z.number().optional(),
  total_files: z.number().optional(),
  current_file: z.string().optional(),
}).passthrough()

const MatchFixtureSchema = z.object({
  match_id: z.number(),
  home_team: z.string(),
  away_team: z.string(),
  home_score: NumberOrNull.optional(),
  away_score: NumberOrNull.optional(),
  status: z.string().optional(),
  kickoff: z.string().optional(),
  has_home_events: z.boolean().optional(),
  has_away_events: z.boolean().optional(),
  has_both_events: z.boolean().optional(),
}).passthrough()

const TeamSummarySchema = z.object({
  team: z.string(),
  passes: z.number().optional(),
  pass_completion_pct: z.number().optional(),
  shots: z.number().optional(),
  shots_on_target: z.number().optional(),
  shot_accuracy_pct: z.number().optional(),
  xg: z.number().optional(),
  goals: z.number().optional(),
  crosses: z.number().optional(),
  take_ons: z.number().optional(),
  successful_take_ons: z.number().optional(),
  take_on_success_pct: z.number().optional(),
  carries: z.number().optional(),
  inferred_carries: z.number().optional(),
  progressive_carries: z.number().optional(),
  carry_final_third_entries: z.number().optional(),
  carry_box_entries: z.number().optional(),
  final_third_entries: z.number().optional(),
  penalty_area_entries: z.number().optional(),
  box_entries: z.number().optional(),
  open_play_box_entries: z.number().optional(),
  set_piece_box_entries: z.number().optional(),
  average_field_position: z.number().optional(),
  defensive_actions: z.number().optional(),
  touches_in_attacking_third: z.number().optional(),
  high_regains: z.number().optional(),
  transition_threat_events: z.number().optional(),
  transition_threat_proxy: z.number().optional(),
  set_piece_actions: z.number().optional(),
  set_piece_shots: z.number().optional(),
  set_piece_goals: z.number().optional(),
  corners: z.number().optional(),
  free_kicks: z.number().optional(),
  throw_ins: z.number().optional(),
  penalties: z.number().optional(),
  cards: z.number().optional(),
  red_cards: z.number().optional(),
  fouls: z.number().optional(),
  interceptions: z.number().optional(),
}).passthrough()

const GridCellSchema = z.object({
  x_bin: z.number(),
  y_bin: z.number(),
  count: z.number(),
}).passthrough()

const ActionPointSchema = z.object({
  x: z.number(),
  y: z.number(),
  end_x: NumberOrNull.optional(),
  end_y: NumberOrNull.optional(),
  minute: NumberOrNull.optional(),
  team: z.string().optional(),
  player: z.string().optional(),
  type: z.string().optional(),
  outcome_type: z.string().optional(),
  label: z.string().optional(),
  successful: z.boolean().optional(),
  event_type: z.string().optional(),
  event_kind: z.string().optional(),
  is_success: z.boolean().optional(),
  is_carry: z.boolean().optional(),
  is_inferred_carry: z.boolean().optional(),
  is_take_on: z.boolean().optional(),
  is_provider_take_on: z.boolean().optional(),
  carry_distance: z.number().optional(),
  carry_seconds: z.number().optional(),
}).passthrough()

const ShotPointSchema = z.object({
  event_index: NumberOrNull.optional(),
  x: z.number(),
  y: z.number(),
  minute: NumberOrNull.optional(),
  player: z.string().optional(),
  team: z.string().optional(),
  type: z.string().optional(),
  outcome_type: z.string().optional(),
  is_goal: z.boolean().optional(),
}).passthrough()

const GoalmouthPointSchema = z.object({
  event_index: NumberOrNull.optional(),
  x: z.number().optional(),
  y: z.number(),
  z: z.number().optional(),
  goal_mouth_horizontal: z.number().optional(),
  goal_mouth_vertical: z.number().optional(),
  goal_mouth_display_x: z.number().optional(),
  goal_mouth_display_y: z.number().optional(),
  goal_mouth_status: z.string().optional(),
  goal_mouth_qualifiers: z.array(z.string()).optional(),
  is_goal_mouth_high: z.boolean().optional(),
  is_goal_mouth_left: z.boolean().optional(),
  is_goal_mouth_right: z.boolean().optional(),
  is_goal_mouth_woodwork: z.boolean().optional(),
  raw_goal_mouth_x: NumberOrNull.optional(),
  raw_goal_mouth_y: NumberOrNull.optional(),
  raw_goal_mouth_z: NumberOrNull.optional(),
  coordinate_source: z.string().optional(),
  on_target_plane: z.boolean().optional(),
  zone: z.string().optional(),
  xg: NumberOrNull.optional(),
  minute: NumberOrNull.optional(),
  player: z.string().optional(),
  team: z.string().optional(),
  type: z.string().optional(),
  outcome_type: z.string().optional(),
  is_goal: z.boolean().optional(),
}).passthrough()

const MomentumPointSchema = z.object({
  minute: z.number(),
  home: z.number(),
  away: z.number(),
  net: z.number(),
}).passthrough()

const PhaseSummarySchema = z.object({
  title: z.string(),
  summary: z.string(),
  metrics: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.null()])).optional(),
}).passthrough()

const MatchMarkerSchema = z.object({
  event_index: NumberOrNull.optional(),
  minute: NumberOrNull.optional(),
  period: NumberOrNull.optional(),
  team: z.string(),
  team_side: z.string().optional(),
  player: z.string().optional(),
  event_type: z.string().optional(),
  marker_type: z.string(),
  card_type: z.string().optional(),
  score_after_event: z.string().optional(),
}).passthrough()

const AttackingLaneSchema = z.object({
  lane: z.string(),
  label: z.string().optional(),
  y_min: z.number().optional(),
  y_max: z.number().optional(),
  y_mid: z.number().optional(),
  count: z.number(),
  weighted_count: z.number().optional(),
  share_pct: z.number().optional(),
  action_share_pct: z.number().optional(),
  final_third_entries: z.number().optional(),
  box_entries: z.number().optional(),
  shots: z.number().optional(),
  shots_on_target: z.number().optional(),
  shot_accuracy_pct: z.number().optional(),
  xg: z.number().optional(),
  goals: z.number().optional(),
  average_x: NumberOrNull.optional(),
  average_y: NumberOrNull.optional(),
  rank: z.number().optional(),
  start_x: z.number().optional(),
  start_y: z.number().optional(),
  end_x: z.number().optional(),
  end_y: z.number().optional(),
}).passthrough()

const PassNetworkPlayerSchema = z.object({
  player: z.string(),
  passes_made: z.number().optional(),
  passes_received: z.number().optional(),
  passes_involved: z.number().optional(),
  avg_x: z.number().optional(),
  avg_y: z.number().optional(),
  avg_made_x: NumberOrNull.optional(),
  avg_made_y: NumberOrNull.optional(),
  avg_received_x: NumberOrNull.optional(),
  avg_received_y: NumberOrNull.optional(),
  xt_involved: z.number().optional(),
}).passthrough()

const PassNetworkConnectionSchema = z.object({
  connection_id: z.string(),
  label: z.string(),
  passer: z.string(),
  receiver: z.string(),
  count: z.number(),
  avg_start_x: z.number(),
  avg_start_y: z.number(),
  avg_receive_x: z.number(),
  avg_receive_y: z.number(),
  total_xt: z.number().optional(),
  progressive_passes: z.number().optional(),
  final_third_entries: z.number().optional(),
  box_entries: z.number().optional(),
  crosses: z.number().optional(),
  forward_distance: z.number().optional(),
}).passthrough()

const PassNetworkSchema = z.object({
  team: z.string().optional(),
  total_passes: z.number().optional(),
  players: z.array(PassNetworkPlayerSchema),
  connections: z.array(PassNetworkConnectionSchema),
}).passthrough()

const SequenceActionSchema = z.object({
  order: z.number(),
  event_index: NumberOrNull.optional(),
  minute: NumberOrNull.optional(),
  player: z.string().optional(),
  team: z.string().optional(),
  type: z.string().optional(),
  outcome_type: z.string().optional(),
  x: NumberOrNull.optional(),
  y: NumberOrNull.optional(),
  end_x: NumberOrNull.optional(),
  end_y: NumberOrNull.optional(),
  label: z.string().optional(),
  is_goal: z.boolean().optional(),
  is_shot: z.boolean().optional(),
  is_set_piece: z.boolean().optional(),
  period: NumberOrNull.optional(),
  xg: NumberOrNull.optional(),
}).passthrough()

const ShotSequenceSchema = z.object({
  sequence_id: z.string(),
  team: z.string(),
  player: z.string().optional(),
  minute: z.number(),
  outcome_type: z.string().optional(),
  is_goal: z.boolean(),
  is_set_piece: z.boolean().optional(),
  start_reason: z.string().optional(),
  actions: z.array(SequenceActionSchema),
}).passthrough()

const RecentPatternSchema = z.object({
  available: z.boolean(),
  reason: z.string().optional(),
  team: z.string().optional(),
  match_count: z.number().optional(),
  recent_match_ids: z.array(z.number()).optional(),
  selected_match: z.record(z.string(), z.number()).optional(),
  recent_average: z.record(z.string(), z.number()).optional(),
  note: z.string().optional(),
}).passthrough()

const ProcessedStoreSchema = z.object({
  exists: z.boolean().optional(),
  events_exists: z.boolean().optional(),
  match_index_exists: z.boolean().optional(),
  rows: z.number().optional(),
  paths: z.record(z.string(), z.string()).optional(),
}).passthrough()

const RenderPhaseMetaSchema = z.union([
  z.string(),
  z.object({
    label: z.string().optional(),
    weight: z.number().optional(),
  }).passthrough(),
])

const RenderMetaSchema = z.object({
  started_at: z.string().optional(),
  completed_at: z.string().optional(),
  duration_ms: z.number().optional(),
  phases: z.array(RenderPhaseMetaSchema).optional(),
  data_source_counts: UnknownRecord.optional(),
  message: z.string().optional(),
}).passthrough()

export const MatchAnalysisResponseSchema = z.object({
  nation: z.string(),
  tier: z.string(),
  season: z.string(),
  processed_store: ProcessedStoreSchema.optional(),
  data_source: z.string().optional(),
  render_meta: RenderMetaSchema.optional(),
  fixtures: z.array(MatchFixtureSchema),
  selected_fixture: z.union([MatchFixtureSchema, z.null()]),
  raw_events: z.array(UnknownRecord),
  available_columns: z.array(z.string()),
  event_count: z.number(),
  analytic_event_count: z.number().optional(),
  team_summaries: z.object({
    home: TeamSummarySchema.optional(),
    away: TeamSummarySchema.optional(),
  }).partial().passthrough(),
  momentum: z.array(MomentumPointSchema),
  match_markers: z.array(MatchMarkerSchema).optional(),
  territory: z.object({
    x_bins: z.number(),
    y_bins: z.number(),
    home: z.array(GridCellSchema),
    away: z.array(GridCellSchema),
  }),
  action_maps: z.object({
    home: z.array(ActionPointSchema),
    away: z.array(ActionPointSchema),
  }),
  shot_maps: z.object({
    home: z.array(ShotPointSchema),
    away: z.array(ShotPointSchema),
  }),
  goalmouth_maps: z.object({
    home: z.array(GoalmouthPointSchema),
    away: z.array(GoalmouthPointSchema),
  }),
  phase_summaries: z.object({
    home: z.array(PhaseSummarySchema),
    away: z.array(PhaseSummarySchema),
  }),
  attacking_direction: z.object({
    home: z.array(AttackingLaneSchema),
    away: z.array(AttackingLaneSchema),
  }).optional(),
  pass_networks: z.object({
    home: PassNetworkSchema,
    away: PassNetworkSchema,
  }).optional(),
  shot_sequences: z.array(ShotSequenceSchema).optional(),
  recent_patterns: z.object({
    home: RecentPatternSchema,
    away: RecentPatternSchema,
  }).optional(),
  xt_analysis: z.object({
    home: UnknownRecord,
    away: UnknownRecord,
  }).passthrough().optional(),
  defensive_analysis: z.object({
    home: UnknownRecord,
    away: UnknownRecord,
  }).optional(),
  set_piece_analysis: z.object({
    home: UnknownRecord,
    away: UnknownRecord,
  }).optional(),
}).passthrough()

export const ExpectedThreatSurfaceResponseSchema = z.object({
  match_id: z.number(),
  home_team: z.string(),
  away_team: z.string(),
  grid_x: z.number(),
  grid_y: z.number(),
  x_edges: z.array(z.number()),
  y_edges: z.array(z.number()),
  x_centres: z.array(z.number()),
  y_centres: z.array(z.number()),
  xt_grid: z.array(z.array(z.number())),
  actions: z.array(UnknownRecord),
  passes: z.array(UnknownRecord),
  model: UnknownRecord,
}).passthrough()

export const ContextualMatchMetricsResponseSchema = z.object({
  match_id: z.number(),
  home_team: z.string(),
  away_team: z.string(),
  pitch_length: z.number().optional(),
  metrics: z.object({
    home: UnknownRecord,
    away: UnknownRecord,
  }),
}).passthrough()

export const PitchControlSnapshotResponseSchema = z.object({
  match_id: z.number(),
  minute: z.union([z.number(), z.null()]).optional(),
  home_team: z.string(),
  away_team: z.string(),
  players: z.array(UnknownRecord),
}).passthrough()

export const SpadlPreviewResponseSchema = z.object({
  match_id: z.number(),
  home_team: z.string(),
  away_team: z.string(),
  count: z.number(),
  columns: z.array(z.string()),
  rows: z.array(UnknownRecord),
  note: z.string().optional(),
}).passthrough()

export type TableResponse = z.infer<typeof TableResponseSchema>
export type BuildProgressEvent = z.infer<typeof BuildProgressEventSchema>
export type RenderMeta = z.infer<typeof RenderMetaSchema>
export type MatchAnalysisResponse = z.infer<typeof MatchAnalysisResponseSchema>
export type ExpectedThreatSurfaceResponse = z.infer<typeof ExpectedThreatSurfaceResponseSchema>
export type ContextualMatchMetricsResponse = z.infer<typeof ContextualMatchMetricsResponseSchema>
export type PitchControlSnapshotResponse = z.infer<typeof PitchControlSnapshotResponseSchema>
export type SpadlPreviewResponse = z.infer<typeof SpadlPreviewResponseSchema>