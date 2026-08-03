/** Types miroirs du contrat API v1 (schémas Pydantic).
 *  Le frontend ne dépend QUE de ces types, jamais d'implémentations internes.
 */

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export type Priority = "low" | "normal" | "high" | "critical";
export type Availability =
  | "unknown"
  | "not_listed"
  | "unavailable"
  | "preorder"
  | "in_stock";

export interface Product {
  uuid: string;
  name: string;
  site: string;
  url: string;
  group: string | null;
  check_interval: number;
  enabled: boolean;
  priority: Priority;
  tags: string[];
  monitorable: boolean;
  availability: Availability | null;
  price: string | null;
  last_checked_at: string | null;
}

export interface ProductInput {
  name: string;
  site: string;
  url: string;
  group?: string | null;
  check_interval: number;
  enabled: boolean;
  priority: Priority;
  tags: string[];
}

export interface CheckNowResult {
  status: "ok" | "error";
  availability: string | null;
  price: string | null;
  page_exists: boolean | null;
  checked_at: string | null;
}

export interface Alert {
  id: number;
  product_uuid: string;
  product_name: string | null;
  site: string | null;
  change_type: string;
  old_value: string | null;
  new_value: string | null;
  price: string | null;
  url: string;
  screenshot_path: string | null;
  screenshot_url: string | null;
  evidence_url: string | null;
  notified: boolean;
  created_at: string;
}

export interface TimelineEntry {
  id: number;
  product_uuid: string;
  event_type: string;
  label: string;
  old_value: string | null;
  new_value: string | null;
  price: string | null;
  created_at: string;
}

export interface Check {
  id: number;
  product_uuid: string;
  status: "ok" | "error";
  availability: string | null;
  response_time_ms: number | null;
  error: string | null;
  checked_at: string;
}

export interface LogEntry {
  id: number;
  time: string;
  level: string;
  logger: string;
  message: string;
}

export interface StatsOverview {
  monitor_active: boolean;
  products_total: number;
  products_enabled: number;
  products_watched: number;
  sites_count: number;
  last_check_at: string | null;
  last_alert_at: string | null;
  uptime_seconds: number;
  checks_total: number;
  alerts_total: number;
  avg_response_ms_24h: number | null;
}

export interface ChecksPerHourPoint {
  hour: string;
  total: number;
  errors: number;
  avg_response_ms: number | null;
}

export interface AlertsPerDayPoint {
  day: string;
  total: number;
}

export interface SiteAvailabilityPoint {
  site: string;
  availability: string;
  count: number;
}

export interface SiteCountPoint {
  site: string;
  count: number;
}

export type DiscoveryStatus =
  | "pending"
  | "imported"
  | "ignored"
  | "blocked"
  | "gone";

export interface Discovery {
  fingerprint: string;
  site: string;
  url: string;
  title: string;
  image_url: string | null;
  price: string | null;
  source: string;
  status: DiscoveryStatus;
  decision_reason: string;
  product_uuid: string | null;
  times_seen: number;
  first_seen_at: string;
  last_seen_at: string;
}

export interface DiscoveryState {
  enabled: boolean;
  mode: "auto" | "review" | "rules";
  scan_interval: number;
  sites: string[];
  counts: Record<string, number>;
  last_discovery_at: string | null;
  last_scan_summary: string | null;
}

export interface ScanReport {
  sites_scanned: number;
  products_seen: number;
  new_products: number;
  imported: number;
  pending: number;
  excluded: number;
  gone: number;
  errors: string[];
  summary: string;
}

export type OfferStatus =
  | "active"
  | "inactive"
  | "not_found"
  | "removed"
  | "archived";

export interface Offer {
  uuid: string;
  product_uuid: string;
  site: string;
  url: string;
  price: string | null;
  currency: string;
  availability: Availability | null;
  status: OfferStatus;
  monitored_uuid: string | null;
  first_seen_at: string;
  last_checked_at: string | null;
  last_changed_at: string | null;
}

export interface CatalogProduct {
  uuid: string;
  name: string;
  brand: string | null;
  collection: string | null;
  edition: string | null;
  category: string | null;
  release_date: string | null;
  image_url: string | null;
  ean: string | null;
  upc: string | null;
  isbn: string | null;
  mpn: string | null;
  manufacturer_sku: string | null;
  manufacturer_ref: string | null;
  tags: string[];
  priority: Priority;
  created_at: string;
  updated_at: string;
  offers: Offer[];
  best_offer_site: string | null;
}

export interface OfferHistoryEntry {
  id: number;
  price: string | null;
  availability: string | null;
  status: OfferStatus;
  recorded_at: string;
}

export interface MatchSuggestion {
  id: number;
  product_uuid: string;
  product_name: string | null;
  candidate_uuid: string;
  candidate_name: string | null;
  score: number;
  method: string;
  reason: string;
  created_at: string;
}

export interface IdentityFieldEntry {
  field: string;
  value: string;
  confidence: number;
  source: string;
}

export interface ProductIdentity {
  fields: IdentityFieldEntry[];
  aliases: string[];
  additional_images: string[];
  search_keys: string[];
}

export interface SearchAttempt {
  id: number;
  site: string;
  key_kind: string;
  key_value: string;
  status: "found" | "not_found" | "error" | "unsupported" | "pending";
  attempts: number;
  confidence: number;
  matched_fields: string[];
  reason: string;
  found_url: string | null;
  first_attempt_at: string;
  last_attempt_at: string;
  next_retry_at: string | null;
}

export interface CrossSiteReport {
  sites_queried: number;
  keys_tried: number;
  candidates_found: number;
  offers_created: number;
  retries_scheduled: number;
  errors: string[];
  summary: string;
}

export interface CatalogState {
  enabled: boolean;
  merge_threshold: number;
  suggestion_floor: number;
  cross_site_search: boolean;
  products: number;
  offers: number;
  pending_suggestions: number;
  methods: string[];
  search_capable_sites: string[];
  identity_strategies: string[];
  pending_retries: number;
}

export interface Monitor {
  site: string;
  display_name: string;
  version: string | null;
  base_url: string | null;
  description: string | null;
  product_count: number;
  watched_count: number;
  last_check_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  avg_response_ms: number | null;
  total_checks: number;
}

export interface Settings {
  telegram: {
    configured: boolean;
    chat_count: number;
    token_preview: string | null;
  };
  screenshots: {
    enabled: boolean;
    available: boolean;
    timeout_ms: number;
    quality: number;
    max_concurrent: number;
    retention_days: number;
    image_format: string;
    full_page: boolean;
    directory: string;
    pending: number;
  };
  log_level: string;
  database: string;
  data_dir: string;
  auth_configured: boolean;
}

export interface TelegramStatus {
  configured: boolean;
  bot_ok: boolean;
  bot_username: string | null;
  chats: { chat_id: string; ok: boolean; title: string | null }[];
}

export interface SystemHealth {
  status: string;
  version: string;
  python_version: string;
  railway_environment: string | null;
  uptime_seconds: number;
  cpu_percent: number | null;
  memory_mb: number | null;
  scheduler_running: boolean;
  watchers_active: number;
  telegram_configured: boolean;
  asyncio_tasks: number;
  database: string;
}

export interface User {
  username: string;
}

/** Messages WebSocket (enveloppe commune). */
export interface WsMessage<T = Record<string, unknown>> {
  type: string;
  payload: T;
  ts: string;
}

/* --- Observabilité (page Santé) ------------------------------------- */

export interface EngineOverview {
  window_hours: number;
  engine_running: boolean;
  plugins_active: number;
  products_watched: number;
  products_total: number;
  offers_total: number;
  canonical_products: number;
  discoveries_today: number;
  discoveries_pending: number;
  alerts_today: number;
  errors_today: number;
  checks_today: number;
  avg_response_ms: number | null;
  avg_response_by_plugin: Record<string, number | null>;
}

export interface PluginHealth {
  site: string;
  display_name: string;
  version: string | null;
  score: number;
  status: "healthy" | "degraded" | "unhealthy" | "observation";
  penalties: Record<string, number>;
  main_issue: string | null;
  products_watched: number;
  products_total: number;
  last_check_at: string | null;
  success_rate: number | null;
  checks: number;
  errors: number;
  avg_response_ms: number | null;
  avg_confidence: number | null;
  unknown_states: number;
  browser_checks: number;
  http_403: number;
  http_404: number;
  http_429: number;
  http_4xx: number;
  http_5xx: number;
  browser_renders: number;
  browser_fallbacks: number;
  captchas: number;
  timeouts: number;
  network_errors: number;
  low_confidence: number;
  locale_mismatch: number;
  pages_missing: number;
  last_error: string | null;
  last_error_at: string | null;
}

export interface ProductEvent {
  kind: string;
  label: string;
  severity: string;
  detail: string;
  created_at: string;
}

export interface ProductHealth {
  uuid: string;
  name: string;
  site: string;
  url: string;
  last_check_at: string | null;
  last_availability: string | null;
  last_alert_at: string | null;
  last_alert_type: string | null;
  last_screenshot: string | null;
  last_evidence: string | null;
  checks_window: number;
  checks_total: number;
  errors: number;
  avg_response_ms: number | null;
  avg_confidence: number | null;
  unknown_states: number;
  browser_checks: number;
  browser_fallbacks: number;
  score: number;
  status: string;
  main_issue: string | null;
  confidence_history: { at: string; confidence: number }[];
  last_error: string | null;
  last_error_at: string | null;
  recent_events: ProductEvent[];
}

export interface DiscoveryHealth {
  found_today: number;
  found_this_week: number;
  imported: number;
  pending: number;
  ignored: number;
  blocked: number;
  last_discovery_at: string | null;
  per_day: { day: string; total: number }[];
  searches_total: number;
  searches_found: number;
  searches_empty: number;
  searches_retrying: number;
}

export interface IntelligenceHealth {
  canonical_products: number;
  offers: number;
  merged_automatically: number;
  pending_validation: number;
  rejected: number;
  avg_confidence: number | null;
  identifiers: Record<string, number>;
}

export interface Anomaly {
  severity: "warning" | "error";
  source: string;
  title: string;
  detail: string;
}

export interface EngineEvent {
  id: number;
  scope: string;
  source: string;
  kind: string;
  label: string;
  severity: string;
  detail: string;
  product_uuid: string | null;
  created_at: string;
}

export interface PhaseTimings {
  http_ms: number | null;
  browser_ms: number | null;
  screenshot_ms: number | null;
  discovery_scan_ms: number | null;
  intelligence_ms: number | null;
}

export interface IncidentStep {
  label: string;
  detail: string;
  severity: string;
  at: string;
}

export interface Incident {
  source: string;
  product_uuid: string | null;
  started_at: string;
  outcome: string;
  steps: IncidentStep[];
}

export interface ScoreComponent {
  name: string;
  key: string;
  score: number;
  status: string;
  weight: number;
}

export interface SystemScore {
  score: number;
  status: string;
  components: ScoreComponent[];
}

export interface StoryEntry {
  at: string;
  site: string;
  label: string;
  detail: string;
  origin: "monitoring" | "discovery" | "intelligence";
}

export interface PropagationStep {
  site: string;
  first_seen_at: string;
  rank: number;
  delay_hours: number;
  url: string;
  price: string | null;
  availability: string | null;
}

export interface ProductMetrics {
  merchants: number;
  first_merchant: string | null;
  first_seen_at: string | null;
  last_merchant: string | null;
  last_merchant_at: string | null;
  changes: number;
  notifications: number;
  screenshots: number;
  price_changes: number;
  back_in_stock: number;
  out_of_stock: number;
  preorders: number;
  invitations: number;
}

export interface StorySearchAttempt {
  site: string;
  key_kind: string;
  key_value: string;
  status: string;
  attempts: number;
  confidence: number;
  reason: string;
  found_url: string | null;
  last_attempt_at: string | null;
  next_retry_at: string | null;
}

export interface ProductStory {
  uuid: string;
  name: string;
  brand: string | null;
  timeline: StoryEntry[];
  propagation: PropagationStep[];
  metrics: ProductMetrics;
  identity: Record<string, string>;
  searches: StorySearchAttempt[];
}

export interface Diagnostics {
  overview: EngineOverview;
  system: SystemScore;
  incidents: Incident[];
  timings: PhaseTimings;
  plugins: PluginHealth[];
  discovery: DiscoveryHealth;
  intelligence: IntelligenceHealth;
  anomalies: Anomaly[];
  history: EngineEvent[];
  charts: {
    checks_per_hour: ChecksPerHourPoint[];
    incidents_per_hour: Record<string, number | string>[];
    confidence_per_hour: { hour: string; avg_confidence: number }[];
    alerts_per_day: AlertsPerDayPoint[];
    discoveries_per_day: { day: string; total: number }[];
  };
}
