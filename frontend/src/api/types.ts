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
