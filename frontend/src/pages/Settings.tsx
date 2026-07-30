/** Paramètres : configuration courante et diagnostic Telegram. */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, CheckCircle2, Send, XCircle } from "lucide-react";
import { settingsApi } from "@/api/endpoints";
import { Badge, Button, Card, PageHeader, Spinner, useToast } from "@/components/ui";
import { ApiError } from "@/api/client";

export default function SettingsPage() {
  const { push } = useToast();
  const settings = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get });
  const telegram = useQuery({
    queryKey: ["settings", "telegram-status"],
    queryFn: settingsApi.telegramStatus,
  });

  const test = useMutation({
    mutationFn: settingsApi.telegramTest,
    onSuccess: (result) =>
      push({
        tone: result.sent ? "success" : "danger",
        title: result.sent ? "Notification de test envoyée" : "Échec de l'envoi",
        description: `${result.recipients} destinataire(s) configuré(s).`,
      }),
    onError: (error) =>
      push({
        tone: "danger",
        title: "Envoi impossible",
        description: error instanceof ApiError ? error.message : undefined,
      }),
  });

  return (
    <>
      <PageHeader
        title="Paramètres"
        description="La configuration vit dans les variables d'environnement (.env / Railway)."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Telegram" actions={
          telegram.data &&
          (telegram.data.bot_ok
            ? <Badge tone="success" dot>Bot opérationnel</Badge>
            : <Badge tone="danger" dot>Bot injoignable</Badge>)
        }>
          {telegram.isLoading && <Spinner />}
          {telegram.data && !telegram.data.configured && (
            <p className="text-sm text-muted">
              Telegram n'est pas configuré. Définissez <code className="text-xs">TELEGRAM_BOT_TOKEN</code> et{" "}
              <code className="text-xs">TELEGRAM_CHAT_ID(S)</code> dans le .env.
            </p>
          )}
          {telegram.data?.configured && (
            <div className="grid gap-3">
              <div className="flex items-center gap-2 text-sm">
                <Bot size={15} className="text-muted" />
                <span className="text-text">
                  {telegram.data.bot_username
                    ? `@${telegram.data.bot_username}`
                    : "Bot inconnu"}
                </span>
              </div>
              <div>
                <p className="mb-1.5 text-xs font-medium text-muted">Destinataires</p>
                <ul className="grid gap-1.5">
                  {telegram.data.chats.map((chat) => (
                    <li key={chat.chat_id}
                        className="flex items-center justify-between rounded-md border border-border bg-surface-2 px-3 py-2 text-sm">
                      <span className="text-text">
                        {chat.title ?? chat.chat_id}
                        <span className="ml-2 text-xs text-faint">{chat.chat_id}</span>
                      </span>
                      {chat.ok
                        ? <CheckCircle2 size={15} className="text-success" />
                        : <XCircle size={15} className="text-danger" />}
                    </li>
                  ))}
                </ul>
              </div>
              <Button variant="primary" icon={<Send size={14} />}
                      loading={test.isPending} onClick={() => test.mutate()}>
                Envoyer une notification de test
              </Button>
            </div>
          )}
        </Card>

        <Card title="Captures d'écran" actions={
          settings.data &&
          (!settings.data.screenshots.enabled
            ? <Badge tone="neutral" dot>Désactivées</Badge>
            : settings.data.screenshots.available
              ? <Badge tone="success" dot>Opérationnelles</Badge>
              : <Badge tone="danger" dot>Chromium indisponible</Badge>)
        }>
          {settings.isLoading && <Spinner />}
          {settings.data && (
            <>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
                <dt className="text-faint">Format</dt>
                <dd className="text-text">
                  {settings.data.screenshots.image_format.toUpperCase()}
                  {settings.data.screenshots.full_page && " · page entière"}
                </dd>
                <dt className="text-faint">Qualité</dt>
                <dd className="text-text">
                  {settings.data.screenshots.quality}
                  {settings.data.screenshots.quality >= 80 && " (rendu 2×)"}
                </dd>
                <dt className="text-faint">Délai maximal</dt>
                <dd className="text-text">
                  {(settings.data.screenshots.timeout_ms / 1000).toFixed(0)} s
                </dd>
                <dt className="text-faint">Captures simultanées</dt>
                <dd className="text-text">{settings.data.screenshots.max_concurrent}</dd>
                <dt className="text-faint">Conservation</dt>
                <dd className="text-text">
                  {settings.data.screenshots.retention_days > 0
                    ? `${settings.data.screenshots.retention_days} jours`
                    : "illimitée"}
                </dd>
                <dt className="text-faint">En file d'attente</dt>
                <dd className="text-text">{settings.data.screenshots.pending}</dd>
                <dt className="text-faint">Dossier</dt>
                <dd className="truncate font-mono text-xs text-text"
                    title={settings.data.screenshots.directory}>
                  {settings.data.screenshots.directory}
                </dd>
              </dl>
              {settings.data.screenshots.enabled &&
                !settings.data.screenshots.available && (
                  <p className="mt-3 rounded-md border border-danger/25 bg-danger/10 px-3 py-2 text-xs text-danger">
                    Chromium n'a pas pu démarrer. Exécutez{" "}
                    <code>playwright install chromium</code>. Les alertes
                    continuent de partir en texte seul.
                  </p>
                )}
            </>
          )}
        </Card>

        <Card title="Configuration">
          {settings.isLoading && <Spinner />}
          {settings.data && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
              <dt className="text-faint">Token Telegram</dt>
              <dd className="font-mono text-xs text-text">
                {settings.data.telegram.token_preview ?? "non défini"}
              </dd>
              <dt className="text-faint">Destinataires</dt>
              <dd className="text-text">{settings.data.telegram.chat_count}</dd>
              <dt className="text-faint">Niveau de log</dt>
              <dd className="text-text">{settings.data.log_level}</dd>
              <dt className="text-faint">Base de données</dt>
              <dd className="text-text">{settings.data.database}</dd>
              <dt className="text-faint">Dossier de données</dt>
              <dd className="truncate font-mono text-xs text-text"
                  title={settings.data.data_dir}>{settings.data.data_dir}</dd>
              <dt className="text-faint">Authentification</dt>
              <dd>
                {settings.data.auth_configured
                  ? <Badge tone="success">Configurée</Badge>
                  : <Badge tone="danger">Non configurée</Badge>}
              </dd>
            </dl>
          )}
        </Card>
      </div>
    </>
  );
}
