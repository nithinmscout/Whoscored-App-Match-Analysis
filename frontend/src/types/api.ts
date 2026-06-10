import type {
  BuildProgressEvent as SchemaBuildProgressEvent,
  ContextualMatchMetricsResponse as SchemaContextualMatchMetricsResponse,
  ExpectedThreatSurfaceResponse as SchemaExpectedThreatSurfaceResponse,
  MatchAnalysisResponse as SchemaMatchAnalysisResponse,
  PitchControlSnapshotResponse as SchemaPitchControlSnapshotResponse,
  SpadlPreviewResponse as SchemaSpadlPreviewResponse,
  TableResponse as SchemaTableResponse,
} from '../schemas/api'

type NonNull<T> = Exclude<T, null | undefined>

export type TableRow = Record<string, string | number | boolean | null>
export type TableResponse = SchemaTableResponse

export type MatchAnalysisResponse = SchemaMatchAnalysisResponse
export type MatchFixture = MatchAnalysisResponse['fixtures'][number]
export type MatchAnalysisTeamSummary = NonNull<NonNull<MatchAnalysisResponse['team_summaries']>['home']>
export type MatchAnalysisGridCell = NonNull<MatchAnalysisResponse['territory']>['home'][number]
export type MatchAnalysisActionPoint = NonNull<MatchAnalysisResponse['action_maps']>['home'][number]
export type MatchAnalysisShotPoint = NonNull<MatchAnalysisResponse['shot_maps']>['home'][number]
export type MatchAnalysisGoalmouthPoint = NonNull<MatchAnalysisResponse['goalmouth_maps']>['home'][number]
export type MatchAnalysisPhaseSummary = NonNull<MatchAnalysisResponse['phase_summaries']>['home'][number]
export type MatchAnalysisMomentumPoint = MatchAnalysisResponse['momentum'][number]
export type MatchAnalysisMarker = NonNull<MatchAnalysisResponse['match_markers']>[number]
export type MatchAnalysisDirectionArrow = NonNull<MatchAnalysisResponse['attacking_direction']>['home'][number]
export type MatchAnalysisShotSequence = NonNull<MatchAnalysisResponse['shot_sequences']>[number]

export type ExpectedThreatSurfaceResponse = SchemaExpectedThreatSurfaceResponse
export type XTArcPass = ExpectedThreatSurfaceResponse['passes'][number]

export type ContextualMatchMetricsResponse = SchemaContextualMatchMetricsResponse
export type ContextualTeamMetrics = ContextualMatchMetricsResponse['metrics']['home']

export type PitchControlSnapshotResponse = SchemaPitchControlSnapshotResponse
export type PitchControlPlayerPoint = PitchControlSnapshotResponse['players'][number]

export type SpadlPreviewResponse = SchemaSpadlPreviewResponse
export type BuildProgressEvent = SchemaBuildProgressEvent
