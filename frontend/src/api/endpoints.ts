/** Fonctions d'accès à l'API v1, une par ressource. */

import { buildQuery, http } from "./client";
import type {
  Alert,
  AlertsPerDayPoint,
  Check,
  CheckNowResult,
  ChecksPerHourPoint,
  LogEntry,
  Monitor,
  Page,
  Product,
  ProductInput,
  Settings,
  SiteAvailabilityPoint,
  SiteCountPoint,
  StatsOverview,
  SystemHealth,
  TelegramStatus,
  TimelineEntry,
  User,
} from "./types";

export type ListParams = Record<string, unknown>;

export const authApi = {
  login: (username: string, password: string) =>
    http.post<User>("/auth/login", { username, password }),
  logout: () => http.post<void>("/auth/logout"),
  me: () => http.get<User>("/auth/me"),
};

export const productsApi = {
  list: (params: ListParams = {}) =>
    http.get<Page<Product>>(`/products${buildQuery(params)}`),
  get: (uuid: string) => http.get<Product>(`/products/${uuid}`),
  create: (body: ProductInput) => http.post<Product>("/products", body),
  update: (uuid: string, body: Partial<ProductInput>) =>
    http.patch<Product>(`/products/${uuid}`, body),
  remove: (uuid: string) => http.delete<void>(`/products/${uuid}`),
  checkNow: (uuid: string) => http.post<CheckNowResult>(`/products/${uuid}/check`),
  timeline: (uuid: string, params: ListParams = {}) =>
    http.get<Page<TimelineEntry>>(`/products/${uuid}/timeline${buildQuery(params)}`),
};

export const alertsApi = {
  list: (params: ListParams = {}) =>
    http.get<Page<Alert>>(`/alerts${buildQuery(params)}`),
};

export const timelineApi = {
  list: (params: ListParams = {}) =>
    http.get<Page<TimelineEntry>>(`/timeline${buildQuery(params)}`),
};

export const checksApi = {
  list: (params: ListParams = {}) =>
    http.get<Page<Check>>(`/checks${buildQuery(params)}`),
};

export const logsApi = {
  list: (params: ListParams = {}) =>
    http.get<Page<LogEntry>>(`/logs${buildQuery(params)}`),
};

export const statsApi = {
  overview: () => http.get<StatsOverview>("/stats/overview"),
  checksPerHour: (hours = 24) =>
    http.get<ChecksPerHourPoint[]>(`/stats/checks-per-hour?hours=${hours}`),
  alertsPerDay: (days = 14) =>
    http.get<AlertsPerDayPoint[]>(`/stats/alerts-per-day?days=${days}`),
  availabilityBySite: () =>
    http.get<SiteAvailabilityPoint[]>("/stats/availability-by-site"),
  productsBySite: () => http.get<SiteCountPoint[]>("/stats/products-by-site"),
};

export const monitorsApi = {
  list: () => http.get<Monitor[]>("/monitors"),
};

export const settingsApi = {
  get: () => http.get<Settings>("/settings"),
  telegramStatus: () => http.get<TelegramStatus>("/settings/telegram/status"),
  telegramTest: () =>
    http.post<{ sent: boolean; recipients: number }>("/settings/telegram/test"),
};

export const healthApi = {
  system: () => http.get<SystemHealth>("/health/system"),
};
