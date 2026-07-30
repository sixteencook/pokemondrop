/** Formulaire de création / édition d'un produit (modal). */

import { useEffect, useState } from "react";
import type { Priority, Product, ProductInput } from "@/api/types";
import { Button, Field, Input, Modal, Select, Toggle } from "@/components/ui";

interface ProductFormModalProps {
  open: boolean;
  product: Product | null; // null = création
  sites: string[];
  saving: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (values: ProductInput) => void;
}

const EMPTY: ProductInput = {
  name: "",
  site: "micromania",
  url: "",
  group: "",
  check_interval: 60,
  enabled: false,
  priority: "normal",
  tags: [],
};

export function ProductFormModal({
  open,
  product,
  sites,
  saving,
  error,
  onClose,
  onSubmit,
}: ProductFormModalProps) {
  const [values, setValues] = useState<ProductInput>(EMPTY);
  const [tagsText, setTagsText] = useState("");

  useEffect(() => {
    if (!open) return;
    if (product) {
      setValues({
        name: product.name,
        site: product.site,
        url: product.url,
        group: product.group ?? "",
        check_interval: product.check_interval,
        enabled: product.enabled,
        priority: product.priority,
        tags: product.tags,
      });
      setTagsText(product.tags.join(", "));
    } else {
      setValues(EMPTY);
      setTagsText("");
    }
  }, [open, product]);

  const set = <K extends keyof ProductInput>(key: K, value: ProductInput[K]) =>
    setValues((current) => ({ ...current, [key]: value }));

  const submit = () => {
    const tags = tagsText
      .split(",")
      .map((tag) => tag.trim().toLowerCase())
      .filter(Boolean);
    onSubmit({ ...values, group: values.group || null, tags });
  };

  return (
    <Modal
      open={open}
      title={product ? "Modifier le produit" : "Ajouter un produit"}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Annuler</Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={!values.name.trim()}
            onClick={submit}
          >
            {product ? "Enregistrer" : "Ajouter"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {error && (
          <p className="rounded-md border border-danger/25 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}
        <Field label="Nom">
          <Input
            value={values.name}
            onChange={(event) => set("name", event.target.value)}
            placeholder="Pokémon 30 Ans UPC Jour"
            autoFocus
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Site">
            <Select value={values.site} onChange={(event) => set("site", event.target.value)}>
              {sites.map((site) => (
                <option key={site} value={site}>{site}</option>
              ))}
            </Select>
          </Field>
          <Field label="Priorité">
            <Select
              value={values.priority}
              onChange={(event) => set("priority", event.target.value as Priority)}
            >
              <option value="low">Basse</option>
              <option value="normal">Normale</option>
              <option value="high">Haute</option>
              <option value="critical">Critique</option>
            </Select>
          </Field>
        </div>
        <Field label="URL de la fiche produit" hint="Laisser vide tant que la page n'existe pas.">
          <Input
            value={values.url}
            onChange={(event) => set("url", event.target.value)}
            placeholder="https://www.micromania.fr/…"
            inputMode="url"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Intervalle (secondes)" hint="Minimum 10 s.">
            <Input
              type="number"
              min={10}
              max={86400}
              value={values.check_interval}
              onChange={(event) => set("check_interval", Number(event.target.value))}
            />
          </Field>
          <Field label="Groupe (comparaison multi-sites)">
            <Input
              value={values.group ?? ""}
              onChange={(event) => set("group", event.target.value)}
              placeholder="pokemon-30-upc-jour"
            />
          </Field>
        </div>
        <Field label="Tags" hint="Séparés par des virgules : pokemon, upc, collector…">
          <Input
            value={tagsText}
            onChange={(event) => setTagsText(event.target.value)}
            placeholder="pokemon, 30-ans, upc"
          />
        </Field>
        <Toggle
          checked={values.enabled}
          onChange={(checked) => set("enabled", checked)}
          label="Surveillance activée"
        />
      </div>
    </Modal>
  );
}
