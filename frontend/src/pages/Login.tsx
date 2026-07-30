import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Boxes } from "lucide-react";
import { useAuth } from "@/auth/AuthProvider";
import { Button, Field, Input } from "@/components/ui";
import { ApiError } from "@/api/client";

export default function LoginPage() {
  const { state, login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (state === "authenticated") return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Connexion impossible.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm animate-fade-up">
        <div className="mb-6 flex flex-col items-center gap-2">
          <div className="rounded-xl border border-border bg-surface p-3">
            <Boxes size={26} className="text-accent-hover" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight">Drop Monitor</h1>
          <p className="text-xs text-muted">Connectez-vous pour accéder au dashboard</p>
        </div>

        <form
          onSubmit={submit}
          className="grid gap-4 rounded-lg border border-border bg-surface p-5"
        >
          {error && (
            <p className="rounded-md border border-danger/25 bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          )}
          <Field label="Utilisateur">
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              autoFocus
            />
          </Field>
          <Field label="Mot de passe">
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            loading={submitting}
            disabled={!username || !password}
            className="mt-1 w-full"
          >
            Se connecter
          </Button>
        </form>
      </div>
    </div>
  );
}
