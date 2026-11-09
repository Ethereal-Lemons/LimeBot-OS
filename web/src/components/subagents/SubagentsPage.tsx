import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { ArrowLeft, Bot, Loader2, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { API_BASE_URL } from "@/lib/api";
import { classifySaveError, saveErrorToastText, type SaveErrorInfo } from "@/lib/save-error";
import { useBeforeUnloadGuard, useUnsaved } from "@/hooks/useUnsaved";
import { useSaveState } from "@/hooks/useSaveState";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  EmptyState,
  Field,
  LoadFailed,
  LoadingRows,
  Note,
  SaveBar,
  SaveErrorNote,
  SettingsPage,
  SettingsSection,
  StatusPill,
  invalidInputClass,
} from "@/components/settings/kit";
import { cn } from "@/lib/utils";

type Subagent = {
  id: string;
  name: string;
  description: string;
  prompt: string;
  tools: string[] | null;
  disallowed_tools: string[];
  model: string;
  max_turns: number | null;
  background: boolean;
  filename: string;
  location: string;
  location_label?: string;
  path: string;
  active: boolean;
  shadowed_by: string | null;
  builtin: boolean;
};

type LocationOption = {
  value: string;
  label: string;
  path: string;
};

type FormState = {
  id?: string;
  name: string;
  description: string;
  prompt: string;
  toolsText: string;
  disallowedToolsText: string;
  model: string;
  maxTurns: string;
  background: boolean;
  location: string;
};

type FieldName = "name" | "description" | "prompt";

const EMPTY_FORM: FormState = {
  name: "",
  description: "",
  prompt: "",
  toolsText: "",
  disallowedToolsText: "",
  model: "inherit",
  maxTurns: "",
  background: false,
  location: "project_limebot",
};

const FALLBACK_LOCATIONS: LocationOption[] = [
  { value: "project_limebot", label: "Project (.limebot/agents)", path: ".limebot/agents" },
  { value: "project_claude", label: "Project (.claude/agents)", path: ".claude/agents" },
  { value: "user_limebot", label: "Personal (~/.limebot/agents)", path: "~/.limebot/agents" },
  { value: "user_claude", label: "Personal (~/.claude/agents)", path: "~/.claude/agents" },
];

function normalizeTools(text: string): string[] | null {
  const values = text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return values.length > 0 ? values : null;
}

function normalizeMaxTurns(text: string): number | null {
  const value = text.trim();
  if (!value) return null;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function formFromSubagent(subagent: Subagent): FormState {
  return {
    id: subagent.id,
    name: subagent.name,
    description: subagent.description,
    prompt: subagent.prompt,
    toolsText: subagent.tools?.join(", ") || "",
    disallowedToolsText: subagent.disallowed_tools?.join(", ") || "",
    model: subagent.model || "inherit",
    maxTurns: subagent.max_turns ? String(subagent.max_turns) : "",
    background: Boolean(subagent.background),
    location: subagent.location,
  };
}

/** Mirrors how the backend turns a name into the saved file name. */
function slugifyFilename(name: string): string {
  const text = name.trim().replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "");
  return (text || "subagent").toLowerCase();
}

const REQUIRED_MESSAGES: Record<FieldName, string> = {
  name: "Give the subagent a name.",
  description: "Describe when LimeBot should choose this subagent.",
  prompt: "Write the instructions this subagent should follow.",
};

function validateForm(form: FormState): Partial<Record<FieldName, string>> {
  const errors: Partial<Record<FieldName, string>> = {};
  if (!form.name.trim()) errors.name = REQUIRED_MESSAGES.name;
  if (!form.description.trim()) errors.description = REQUIRED_MESSAGES.description;
  if (!form.prompt.trim()) errors.prompt = REQUIRED_MESSAGES.prompt;
  return errors;
}

type ConfirmState =
  | { kind: "delete"; subagent: Subagent }
  | { kind: "discard" }
  | null;

export function SubagentsPage() {
  const [subagents, setSubagents] = useState<Subagent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [locationOptions, setLocationOptions] = useState<LocationOption[]>([]);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(false);
  const [listError, setListError] = useState<SaveErrorInfo | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [attempted, setAttempted] = useState(false);

  const { dirty, markSaved, restore } = useUnsaved(form);
  const { saving, error: saveError, run, clearError } = useSaveState("this subagent");
  useBeforeUnloadGuard(editing && dirty);

  const loadSubagents = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get(`${API_BASE_URL}/api/subagents`);
      setSubagents(res.data.subagents || []);
      setLocationOptions(res.data.location_options || []);
      setLoadError(null);
    } catch (error: unknown) {
      console.error("Failed to load subagents:", error);
      setLoadError(error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSubagents();
  }, [loadSubagents]);

  const filteredSubagents = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return subagents;
    return subagents.filter((subagent) =>
      [
        subagent.name,
        subagent.description,
        subagent.prompt,
        subagent.location,
        ...(subagent.tools || []),
      ]
        .join(" ")
        .toLowerCase()
        .includes(query)
    );
  }, [search, subagents]);

  const locations = locationOptions.length > 0 ? locationOptions : FALLBACK_LOCATIONS;
  const errors = useMemo(() => validateForm(form), [form]);
  const shownError = (field: FieldName) => (attempted || touched[field] ? errors[field] ?? null : null);

  const openEditor = (next: FormState) => {
    setForm(next);
    markSaved(next);
    setTouched({});
    setAttempted(false);
    clearError();
    setListError(null);
    setEditing(true);
  };

  const openCreateDialog = () => openEditor(EMPTY_FORM);
  const openEditDialog = (subagent: Subagent) => openEditor(formFromSubagent(subagent));

  const closeEditor = () => {
    setEditing(false);
    setForm(EMPTY_FORM);
    clearError();
  };

  const requestClose = () => {
    if (dirty && !saving) setConfirm({ kind: "discard" });
    else closeEditor();
  };

  const discardChanges = () => {
    const base = restore();
    if (base) setForm(base);
    setTouched({});
    setAttempted(false);
    clearError();
  };

  const saveSubagent = async () => {
    const invalid = (Object.keys(errors) as FieldName[])[0];
    if (invalid) {
      setAttempted(true);
      document.getElementById(`subagent-${invalid}`)?.focus();
      return;
    }
    const wasNew = !form.id;
    const ok = await run(async () => {
      const payload = {
        name: form.name,
        description: form.description,
        prompt: form.prompt,
        tools: normalizeTools(form.toolsText),
        disallowed_tools: normalizeTools(form.disallowedToolsText),
        model: form.model || "inherit",
        max_turns: normalizeMaxTurns(form.maxTurns),
        background: form.background,
        location: form.location,
      };
      const url = form.id
        ? `${API_BASE_URL}/api/subagents/${encodeURIComponent(form.id)}`
        : `${API_BASE_URL}/api/subagents`;
      const res = form.id
        ? await axios.put(url, payload)
        : await axios.post(url, payload);
      setSubagents(res.data.subagents || []);
      setLocationOptions(res.data.location_options || []);
    });
    if (ok) {
      markSaved();
      toast.success(wasNew ? "Subagent created." : "Subagent saved.");
      closeEditor();
    }
  };

  const deleteSubagent = async (subagent: Subagent) => {
    setDeletingId(subagent.id);
    try {
      const res = await axios.delete(
        `${API_BASE_URL}/api/subagents/${encodeURIComponent(subagent.id)}`
      );
      setSubagents(res.data.subagents || []);
      setLocationOptions(res.data.location_options || []);
      setListError(null);
      toast.success(`Deleted ${subagent.name}.`);
    } catch (error: unknown) {
      console.error("Failed to delete subagent:", error);
      const info = classifySaveError(error, "that subagent");
      setListError({ ...info, title: `Could not delete ${subagent.name}`, hint: info.category === "network" ? info.hint : `${info.title}. ${info.hint}` });
      toast.error(saveErrorToastText(info));
    } finally {
      setDeletingId(null);
    }
  };

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // ── editor ───────────────────────────────────────────────────────────────

  if (editing) {
    const fileName = slugifyFilename(form.name);
    const collides = subagents.some(
      (item) =>
        !item.builtin &&
        item.location === form.location &&
        item.filename === fileName &&
        item.id !== form.id
    );
    const maxTurnsIgnored = form.maxTurns.trim() !== "" && normalizeMaxTurns(form.maxTurns) === null;
    const locationPath = locations.find((option) => option.value === form.location)?.path;

    return (
      <SettingsPage
        title={form.id ? "Edit subagent" : "New subagent"}
        description="Keep the description focused on when LimeBot should choose this helper. That description is what drives auto-selection."
        icon={Bot}
        actions={
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={requestClose}>
            <ArrowLeft className="h-3.5 w-3.5" />
            All subagents
          </Button>
        }
        saveBar={
          <SaveBar
            dirty={dirty}
            saving={saving}
            onSave={() => void saveSubagent()}
            onDiscard={discardChanges}
            error={saveError}
            onDismissError={clearError}
            saveLabel={form.id ? "Save changes" : "Create subagent"}
            unsavedLabel={form.id ? "Unsaved changes" : "Not created yet"}
          />
        }
      >
        <SettingsSection title="Basics" description="What this helper is called, where it is stored, and when it should be picked.">
          <Field
            label="Name"
            htmlFor="subagent-name"
            error={shownError("name")}
            hint={
              form.name.trim() ? (
                <>
                  Saved as <span className="font-mono">{fileName}.md</span>
                  {locationPath ? <> in <span className="font-mono">{locationPath}</span></> : null}.
                </>
              ) : (
                "Short and lowercase works best, for example code-reviewer."
              )
            }
          >
            <Input
              id="subagent-name"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
              placeholder="code-reviewer"
              aria-invalid={Boolean(shownError("name"))}
              className={cn(shownError("name") && invalidInputClass)}
            />
            {collides && (
              <Note tone="warn">
                {form.id
                  ? "A subagent with this file name already exists in this location, so saving will be rejected. Pick another name or location."
                  : "A subagent with this file name already exists in this location. Creating it will replace the existing one."}
              </Note>
            )}
          </Field>

          <Field label="Location" htmlFor="subagent-location" hint="Project locations are shared with the repository. Personal locations apply to you everywhere.">
            <Select value={form.location} onValueChange={(value) => setField("location", value)}>
              <SelectTrigger id="subagent-location">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {locations.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Description" htmlFor="subagent-description" error={shownError("description")}>
            <Textarea
              id="subagent-description"
              value={form.description}
              onChange={(e) => setField("description", e.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, description: true }))}
              className={cn("min-h-[90px]", shownError("description") && invalidInputClass)}
              placeholder="Review code for bugs, regressions, and missing tests."
              aria-invalid={Boolean(shownError("description"))}
            />
          </Field>
        </SettingsSection>

        <SettingsSection title="Instructions" description="The system prompt this subagent runs with.">
          <Field label="System prompt" htmlFor="subagent-prompt" error={shownError("prompt")}>
            <Textarea
              id="subagent-prompt"
              value={form.prompt}
              onChange={(e) => setField("prompt", e.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, prompt: true }))}
              className={cn("min-h-[180px] font-mono text-sm", shownError("prompt") && invalidInputClass)}
              placeholder="Focus on concrete findings first. Keep the report concise and actionable."
              aria-invalid={Boolean(shownError("prompt"))}
            />
          </Field>
        </SettingsSection>

        <SettingsSection title="Tool access" description="Separate tool names with commas.">
          <Field label="Tools" htmlFor="subagent-tools" optional hint="Leave empty to inherit the main agent's tools.">
            <Input
              id="subagent-tools"
              value={form.toolsText}
              onChange={(e) => setField("toolsText", e.target.value)}
              placeholder="Read, Grep, Bash"
            />
          </Field>
          <Field label="Disallowed tools" htmlFor="subagent-disallowed-tools" optional hint="Blocked even when the tools above would allow them.">
            <Input
              id="subagent-disallowed-tools"
              value={form.disallowedToolsText}
              onChange={(e) => setField("disallowedToolsText", e.target.value)}
              placeholder="Delete, Write"
            />
          </Field>
        </SettingsSection>

        <SettingsSection title="Run settings" description="Model, turn limit and whether it keeps running while you chat.">
          <Field label="Model" htmlFor="subagent-model" hint="Use inherit to follow the main agent's model.">
            <Input
              id="subagent-model"
              value={form.model}
              onChange={(e) => setField("model", e.target.value)}
              placeholder="inherit"
            />
          </Field>
          <Field
            label="Max turns"
            htmlFor="subagent-max-turns"
            optional
            hint={
              maxTurnsIgnored ? (
                <span className="text-amber-500">That is not a whole number above 0, so no limit will be set.</span>
              ) : (
                "Stop after this many model turns. Leave empty for no limit."
              )
            }
          >
            <Input
              id="subagent-max-turns"
              inputMode="numeric"
              value={form.maxTurns}
              onChange={(e) => setField("maxTurns", e.target.value)}
              placeholder="8"
              className="sm:max-w-[10rem]"
            />
          </Field>
          <Field label="Background" htmlFor="subagent-background" inline hint="Return later instead of waiting for the result.">
            <Switch
              id="subagent-background"
              checked={form.background}
              onCheckedChange={(checked) => setField("background", checked)}
            />
          </Field>
        </SettingsSection>

        {renderConfirm()}
      </SettingsPage>
    );
  }

  // ── list ─────────────────────────────────────────────────────────────────

  function renderConfirm() {
    return (
      <AlertDialog open={confirm !== null} onOpenChange={(open) => { if (!open) setConfirm(null); }}>
        <AlertDialogContent>
          {confirm?.kind === "delete" && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete {confirm.subagent.name}?</AlertDialogTitle>
                <AlertDialogDescription>
                  Delete the subagent "{confirm.subagent.name}" from {confirm.subagent.location_label || confirm.subagent.location}? The file is removed and this cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() => {
                    const target = confirm.subagent;
                    setConfirm(null);
                    void deleteSubagent(target);
                  }}
                >
                  Delete
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
          {confirm?.kind === "discard" && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>Discard your changes?</AlertDialogTitle>
                <AlertDialogDescription>
                  You have unsaved edits to this subagent. Leaving now throws them away.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep editing</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() => {
                    setConfirm(null);
                    closeEditor();
                  }}
                >
                  Discard
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>
    );
  }

  const newButton = (
    <Button onClick={openCreateDialog} size="sm" className="gap-1.5">
      <Plus className="h-4 w-4" />
      New Subagent
    </Button>
  );

  let body;
  if (loading) {
    body = <LoadingRows rows={4} className="mt-6" label="Loading subagents" />;
  } else if (loadError && subagents.length === 0) {
    body = <LoadFailed error={loadError} subject="your subagents" onRetry={() => void loadSubagents()} />;
  } else if (subagents.length === 0) {
    body = (
      <EmptyState
        icon={Bot}
        title="No subagents yet"
        description={
          <>
            Start with a specialist like <span className="font-mono">code-reviewer</span>, <span className="font-mono">planner</span>, or <span className="font-mono">researcher</span>.
          </>
        }
        action={newButton}
      />
    );
  } else {
    body = (
      <div className="mt-4 space-y-4">
        {listError && (
          <SaveErrorNote error={listError} onDismiss={() => setListError(null)} />
        )}
        <Note tone="info" title="Try one out">
          Create a subagent here, then ask LimeBot something like: <span className="font-mono">Use the code-reviewer subagent to inspect my auth changes</span> or simply <span className="font-mono">Review my auth changes for bugs and missing tests</span>.
        </Note>
        <div className="relative w-full sm:w-72">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Search subagents"
            placeholder="Search subagents..."
            className="h-9 pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {filteredSubagents.length === 0 ? (
          <EmptyState icon={Search} title={`No subagents match "${search}"`} description="Try a different word, or clear the search to see all of them." />
        ) : (
          <ul className="divide-y divide-border/50 border-y border-border/50">
            {filteredSubagents.map((subagent) => (
              <li key={subagent.id} className={cn("flex items-start gap-3 py-4", !subagent.active && "opacity-70")}>
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Bot className="h-4 w-4" />
                </span>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h3 className="break-all text-sm font-semibold text-foreground">{subagent.name}</h3>
                    {subagent.active ? (
                      <StatusPill tone="ok">Active</StatusPill>
                    ) : (
                      <StatusPill tone="muted">Shadowed</StatusPill>
                    )}
                    {subagent.builtin && <StatusPill tone="info">Built-in</StatusPill>}
                    <span className="text-[11px] text-muted-foreground">
                      {subagent.location_label || subagent.location}
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed text-foreground/90">{subagent.description}</p>
                  <p className="line-clamp-2 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                    {subagent.prompt}
                  </p>
                  <div className="flex flex-wrap gap-1.5 pt-0.5 font-mono text-[11px] text-muted-foreground">
                    <span className="rounded bg-muted px-1.5 py-0.5">model: {subagent.model || "inherit"}</span>
                    {subagent.max_turns ? (
                      <span className="rounded bg-muted px-1.5 py-0.5">max_turns: {subagent.max_turns}</span>
                    ) : null}
                    {subagent.background ? (
                      <span className="rounded bg-muted px-1.5 py-0.5">background</span>
                    ) : null}
                    {subagent.tools === null ? (
                      <span className="rounded bg-muted px-1.5 py-0.5">tools: inherit</span>
                    ) : subagent.tools.length === 0 ? (
                      <span className="rounded bg-muted px-1.5 py-0.5">tools: none</span>
                    ) : (
                      subagent.tools.map((tool) => (
                        <span key={tool} className="rounded bg-muted px-1.5 py-0.5 text-foreground/80">
                          {tool}
                        </span>
                      ))
                    )}
                    {subagent.disallowed_tools?.map((tool) => (
                      <span key={`blocked-${tool}`} className="rounded bg-red-500/10 px-1.5 py-0.5 text-red-500">
                        blocked: {tool}
                      </span>
                    ))}
                  </div>
                  <p className="break-all font-mono text-[11px] text-muted-foreground/70">
                    {subagent.path}
                    {!subagent.active && subagent.shadowed_by ? ` · shadowed by: ${subagent.shadowed_by}` : ""}
                  </p>
                </div>
                {!subagent.builtin && (
                  <div className="flex shrink-0 gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => openEditDialog(subagent)}
                      title="Edit subagent"
                      aria-label={`Edit ${subagent.name}`}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 text-muted-foreground hover:text-red-500"
                      disabled={deletingId === subagent.id}
                      onClick={() => setConfirm({ kind: "delete", subagent })}
                      title="Delete subagent"
                      aria-label={`Delete ${subagent.name}`}
                    >
                      {deletingId === subagent.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <SettingsPage
      title="Subagents"
      icon={Bot}
      description="Lightweight Claude-style specialists. The main agent can choose one automatically when a task matches its description, or you can call one explicitly."
      width="max-w-5xl"
      actions={!loading && !loadError && subagents.length > 0 ? newButton : undefined}
    >
      {body}
      {renderConfirm()}
    </SettingsPage>
  );
}
