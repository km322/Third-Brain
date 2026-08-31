// Shared TypeScript types mirroring the backend API contracts (app/models + app/schemas).
// Keep in sync with apps/api/app/models/enums.py.

export type OrgRole = "owner" | "admin" | "editor" | "viewer";
export type MembershipStatus = "active" | "invited" | "suspended";
export type TeamRole = "lead" | "member";
export type Visibility = "private" | "team" | "org" | "public";
export type PermissionLevel = "none" | "viewer" | "editor" | "manager";
export type PrincipalType = "user" | "team";
export type ResourceType = "collection" | "document";
export type DocumentStatus =
  "pending" | "processing" | "indexed" | "failed" | "archived" | "quarantined";
export type SourceType = "file" | "text" | "url" | "connector";
export type ConnectorType =
  "openai" | "azure_openai" | "ollama" | "custom" | "anthropic" | "google";
export type ConnectorPurpose = "embedding" | "completion";
export type UsageKind = "embedding" | "completion" | "search" | "ingest";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface User {
  id: string;
  email: string;
  full_name?: string | null;
  avatar_url?: string | null;
  is_active: boolean;
  last_login_at?: string | null;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  created_at: string;
}

export interface Membership {
  id: string;
  org_id: string;
  user_id: string;
  role: OrgRole;
  status: MembershipStatus;
  user?: User;
}

export interface Team {
  id: string;
  org_id: string;
  name: string;
  slug: string;
  description?: string | null;
  /** Parent team in the hierarchy, or `null` for a root (top-level) team. */
  parent_team_id: string | null;
  member_count?: number;
}

/** The user fields inlined on a team membership row. */
export interface TeamMemberUser {
  id: string;
  email: string;
  full_name?: string | null;
  avatar_url?: string | null;
}

/** A single membership of a team, with its role and linked user. */
export interface TeamMember {
  id: string;
  user_id: string;
  role: TeamRole;
  created_at: string;
  user?: TeamMemberUser | null;
}

/** A team plus its resolved membership roster and direct sub-teams. */
export interface TeamDetail extends Team {
  members: TeamMember[];
  children: Team[];
}

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  rate_limit_per_minute: number;
  revoked: boolean;
  last_used_at?: string | null;
  expires_at?: string | null;
  created_at: string;
}

export interface Collection {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  visibility: Visibility;
  default_permission: PermissionLevel;
  embedding_model: string;
  document_count: number;
  created_at: string;
}

export interface DocumentItem {
  id: string;
  collection_id: string;
  title: string;
  source_type: SourceType;
  source_uri?: string | null;
  mime_type?: string | null;
  status: DocumentStatus;
  visibility?: Visibility | null;
  chunk_count: number;
  size_bytes: number;
  error?: string | null;
  indexed_at?: string | null;
  created_at: string;
  verification_status: VerificationStatus;
  verified_at?: string | null;
  expires_at?: string | null;
  sensitivity: SensitivityLevel;
  /** Provenance marker; "mcp" means an agent wrote it via the MCP add_knowledge tool. */
  via?: string | null;
  /** Capture category (e.g. "decision") set via the MCP add_knowledge doc_type. */
  doc_type?: string | null;
  /** Capability URL for an image document's original bytes; null for non-images. */
  image_url?: string | null;
}

/** One indexed chunk of a document, in reading order. */
export interface DocumentChunk {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  metadata?: Record<string, unknown>;
}

/** A document's full source text, as served to the editor. */
export interface DocumentContent {
  id: string;
  title: string;
  content: string;
  mime_type?: string | null;
  source_type: SourceType;
  /** Whether THIS caller may save edits (backend authz, not org role). */
  editable: boolean;
  permission: PermissionLevel;
  chunk_count: number;
}

/** One redacted excerpt of a detected secret. Never contains the raw value. */
export interface SecretFindingSample {
  redacted: string;
  line: number;
}

/** A group of matches from one secret detector (e.g. aws-access-key-id). */
export interface SecretFinding {
  detector: string;
  label: string;
  severity: "high" | "medium" | "low";
  occurrences: number;
  samples: SecretFindingSample[];
}

/** One user who would be able to see a quarantined document once indexed. */
export interface AudienceEntry {
  user_id: string;
  name: string | null;
  email: string;
  permission: PermissionLevel;
  via: string;
}

/** Response of `GET /documents/{id}/review` for a quarantined document. */
export interface QuarantineReview {
  document: {
    id: string;
    title: string;
    status: DocumentStatus;
    /** Document-level visibility override; null means it inherits the collection's. */
    visibility: Visibility | null;
    source_type: SourceType;
    mime_type: string | null;
    size_bytes: number;
    created_at: string;
  };
  findings: SecretFinding[];
  scanned_at: string | null;
  truncated: boolean;
  collection: {
    id: string;
    name: string;
    visibility: Visibility;
    default_permission: PermissionLevel;
  };
  audience: {
    total_users: number;
    /** Users with access grouped by their effective permission level. */
    permission_counts: { viewer: number; editor: number; manager: number };
    truncated: boolean;
    note: string | null;
    /**
     * Individual users with access, or `[]` when the caller lacks manager
     * permission on the collection (identities are redacted; `note` explains).
     */
    entries: AudienceEntry[];
  };
}

export interface AccessGrant {
  id: string;
  resource_type: ResourceType;
  resource_id: string;
  principal_type: PrincipalType;
  principal_id: string;
  permission: PermissionLevel;
  principal_name?: string;
}

export interface Connector {
  id: string;
  name: string;
  type: ConnectorType;
  purpose: ConnectorPurpose;
  model: string;
  is_default: boolean;
  enabled: boolean;
  created_at: string;
}

export interface Citation {
  document_id: string;
  document_title: string;
  collection_id: string;
  chunk_index: number;
  score: number;
  snippet: string;
}

export interface SearchResult {
  query: string;
  hits: Citation[];
  answers: AnswerMatch[];
  insight_id?: string | null;
}

// Knowledge graph. Nodes are documents (permission-scoped: only documents the
// caller may view appear, and edges only ever connect two visible documents).
// Edges are undirected and de-duplicated (each pair emitted once with the
// lexicographically smaller id as `source`).
export interface GraphNode {
  id: string;
  title: string;
  collection_id: string;
  collection_name: string;
  chunk_count: number;
  source_type: SourceType;
  created_at: string;
  degree: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_visible: number;
  limit: number;
  min_similarity: number;
}

export interface GraphNeighbors {
  center_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  insight_id?: string | null;
  conversation_id?: string | null;
  web_sources: WebSource[];
}

export interface UsagePoint {
  date: string;
  requests: number;
  tokens: number;
  cost_usd: number;
}

export interface UsageSummary {
  total_requests: number;
  total_tokens: number;
  total_cost_usd: number;
  by_day: UsagePoint[];
  by_kind: { kind: UsageKind; requests: number; tokens: number; cost_usd: number }[];
}

export interface AuditLogEntry {
  id: string;
  action: string;
  actor_user_id?: string | null;
  actor_email?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  ip_address?: string | null;
  created_at: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

/** Body for `POST /auth/change-password`; responds with fresh {@link AuthTokens}. */
export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

/**
 * Response of `POST /orgs/members/{id}/reset-password`. The temporary password
 * is returned exactly once; only its hash is stored server-side.
 */
export interface MemberPasswordReset {
  temporary_password: string;
}

export interface CurrentUser {
  user: User;
  organizations: Organization[];
  active_org: Organization;
  role: OrgRole;
}

// ---------------------------------------------------------------------------
// Knowledge-platform feature types (mirror apps/api/app/schemas + enums).
// ---------------------------------------------------------------------------
export type VerificationStatus = "unverified" | "verified" | "stale";
export type SensitivityLevel = "none" | "pii" | "confidential";
export type DataSourceKind =
  "local_folder" | "google_drive" | "slack" | "github" | "notion" | "confluence";
export type DataSourceStatus = "active" | "paused" | "syncing" | "error";
export type ExternalPrincipalKind = "user" | "group";
export type FeedbackRating = "up" | "down";
export type EntityKind = "person" | "org" | "product" | "project" | "location" | "other";
export type SsoProtocol = "oidc" | "saml";
export type InviteStatus = "pending" | "accepted" | "revoked" | "expired";

// -- Data-source connectors --
export interface DataSource {
  id: string;
  name: string;
  kind: DataSourceKind;
  status: DataSourceStatus;
  collection_id: string;
  config: Record<string, unknown>;
  has_secret: boolean;
  default_visibility: Visibility;
  sync_interval_minutes?: number | null;
  last_synced_at?: string | null;
  last_error?: string | null;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface SyncResult {
  created: number;
  updated: number;
  deleted: number;
  skipped: number;
}

export interface ExternalPrincipalItem {
  provider: string;
  external_id: string;
  kind: ExternalPrincipalKind;
  document_count: number;
  mapped: boolean;
  mapped_user_id?: string | null;
  mapped_team_id?: string | null;
}

export interface Identity {
  id: string;
  provider: string;
  external_id: string;
  kind: ExternalPrincipalKind;
  user_id?: string | null;
  team_id?: string | null;
  created_at: string;
}

// -- Verified answers --
export interface Answer {
  id: string;
  question: string;
  answer: string;
  collection_id?: string | null;
  visibility: Visibility;
  verification_status: VerificationStatus;
  verified_at?: string | null;
  expires_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnswerMatch {
  id: string;
  question: string;
  answer: string;
  verification_status: VerificationStatus;
}

// -- Feedback / knowledge gaps --
export interface KnowledgeGapReport {
  window_days: number;
  total_queries: number;
  answered: number;
  unanswered: number;
  positive: number;
  negative: number;
  answered_rate?: number | null;
  query_text_retained: boolean;
  top_gaps: string[];
}

// -- DLP / oversharing --
export interface OversharingItem {
  document_id: string;
  title: string;
  sensitivity: SensitivityLevel;
  effective_visibility: Visibility;
  collection_id: string;
  collection_name: string;
}

export interface OversharingReport {
  items: OversharingItem[];
  summary: { pii: number; confidential: number };
  total_oversharing: number;
}

// -- Conversations / web grounding --
export interface WebSource {
  title: string;
  url: string;
  snippet: string;
}

export interface Conversation {
  id: string;
  title?: string | null;
  web_enabled: boolean;
  last_message_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[] | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[];
}

// -- Entities --
export interface Entity {
  id: string;
  kind: EntityKind;
  name: string;
  document_count: number;
}

// -- Enterprise identity --
export interface SsoConnection {
  id: string;
  protocol: SsoProtocol;
  name: string;
  enabled: boolean;
  email_domain?: string | null;
  config: Record<string, unknown>;
  has_secret: boolean;
  default_role: OrgRole;
  created_at: string;
}

export interface Invite {
  id: string;
  email: string;
  role: OrgRole;
  status: InviteStatus;
  expires_at: string;
  created_at: string;
}

/** A pending CLI device-authorization request, as shown on the /activate page. */
export interface DeviceAuthPending {
  client_name: string;
  requested_scopes: string[];
  created_at: string;
  expires_at: string;
}

export interface ScimToken {
  id: string;
  name: string;
  token_prefix: string;
  revoked: boolean;
  last_used_at?: string | null;
  created_at: string;
}

export interface ScimTokenCreated extends ScimToken {
  token: string;
}

/** Metadata carried on the final citations frame of a streamed chat. */
export interface ChatStreamMeta {
  insight_id?: string | null;
  conversation_id?: string | null;
  web_sources?: WebSource[];
}
