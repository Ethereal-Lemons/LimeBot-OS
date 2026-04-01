import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Bot, Pencil, Plus, Search, Sparkles, Trash2 } from "lucide-react";

import { API_BASE_URL } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

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

export function SubagentsPage() {
  const [subagents, setSubagents] = useState<Subagent[]>([]);
  const [loading, setLoading] = useState(true);
  const [locationOptions, setLocationOptions] = useState<LocationOption[]>([]);
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  const loadSubagents = async () => {
    try {
      setLoading(true);
      const res = await axios.get(`${API_BASE_URL}/api/subagents`);
      setSubagents(res.data.subagents || []);
      setLocationOptions(res.data.location_options || []);
      setError(null);
    } catch (err: any) {
      console.error("Failed to load subagents:", err);
      setError(err?.response?.data?.detail || "Failed to load subagents.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSubagents();
  }, []);

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

  const openCreateDialog = () => {
    setForm(EMPTY_FORM);
    setError(null);
    setDialogOpen(true);
  };

  const openEditDialog = (subagent: Subagent) => {
    setForm(formFromSubagent(subagent));
    setError(null);
    setDialogOpen(true);
  };

  const saveSubagent = async () => {
    try {
      setSaving(true);
      setError(null);
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
      setDialogOpen(false);
      setForm(EMPTY_FORM);
    } catch (err: any) {
      console.error("Failed to save subagent:", err);
      setError(err?.response?.data?.detail || "Failed to save subagent.");
    } finally {
      setSaving(false);
    }
  };

  const deleteSubagent = async (subagent: Subagent) => {
    const confirmed = window.confirm(
      `Delete the subagent "${subagent.name}" from ${subagent.location_label || subagent.location}?`
    );
    if (!confirmed) return;

    try {
      const res = await axios.delete(
        `${API_BASE_URL}/api/subagents/${encodeURIComponent(subagent.id)}`
      );
      setSubagents(res.data.subagents || []);
      setLocationOptions(res.data.location_options || []);
      setError(null);
    } catch (err: any) {
      console.error("Failed to delete subagent:", err);
      setError(err?.response?.data?.detail || "Failed to delete subagent.");
    }
  };

  return (
    <div className="h-full overflow-y-auto p-6 md:p-8 bg-background/50">
      <div className="max-w-6xl mx-auto space-y-8">
        <header className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Bot className="h-7 w-7 text-primary" />
              Subagents
            </h1>
            <p className="text-muted-foreground mt-1 max-w-3xl">
              Lightweight Claude-style specialists. The main agent can choose one automatically when a task matches its description, or you can call one explicitly.
            </p>
          </div>
          <div className="flex w-full lg:w-auto gap-3">
            <div className="relative flex-1 lg:w-72">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search subagents..."
                className="pl-9 bg-background/50"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <Button onClick={openCreateDialog} className="gap-2">
              <Plus className="h-4 w-4" />
              New Subagent
            </Button>
          </div>
        </header>

        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <Card className="border-border/70 bg-card/60">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              How To Test
            </CardTitle>
            <CardDescription>
              Create a subagent here, then ask LimeBot something like: <span className="font-mono">Use the code-reviewer subagent to inspect my auth changes</span> or simply <span className="font-mono">Review my auth changes for bugs and missing tests</span>.
            </CardDescription>
          </CardHeader>
        </Card>

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-56 rounded-xl bg-muted/50 animate-pulse" />
            ))}
          </div>
        ) : filteredSubagents.length === 0 ? (
          <Card className="border-dashed border-border/70 bg-card/50">
            <CardContent className="py-12 text-center text-muted-foreground">
              <div className="inline-flex items-center justify-center p-4 rounded-full bg-muted mb-4">
                <Bot className="h-6 w-6 opacity-60" />
              </div>
              <p className="font-medium text-foreground">No subagents yet.</p>
              <p className="mt-2 text-sm">
                Start with a specialist like <span className="font-mono">code-reviewer</span>, <span className="font-mono">planner</span>, or <span className="font-mono">researcher</span>.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {filteredSubagents.map((subagent) => (
              <Card
                key={subagent.id}
                className="border-border hover:border-primary/30 transition-colors group"
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-2">
                      <CardTitle className="text-base flex items-center gap-2">
                        {subagent.name}
                        {subagent.active ? (
                          <Badge variant="outline" className="border-emerald-500/30 text-emerald-500">
                            Active
                          </Badge>
                        ) : (
                          <Badge variant="secondary">Shadowed</Badge>
                        )}
                        <Badge variant="outline">
                          {subagent.location_label || subagent.location}
                        </Badge>
                        {subagent.builtin && (
                          <Badge variant="secondary">Built-in</Badge>
                        )}
                      </CardTitle>
                      <CardDescription>{subagent.description}</CardDescription>
                    </div>
                    {!subagent.builtin && (
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={() => openEditDialog(subagent)}
                          title="Edit subagent"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={() => deleteSubagent(subagent)}
                          title="Delete subagent"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="text-sm text-muted-foreground line-clamp-5 whitespace-pre-wrap">
                    {subagent.prompt}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">model: {subagent.model || "inherit"}</Badge>
                    {subagent.max_turns ? (
                      <Badge variant="outline">max_turns: {subagent.max_turns}</Badge>
                    ) : null}
                    {subagent.background ? (
                      <Badge variant="outline">background</Badge>
                    ) : null}
                    {subagent.tools === null ? (
                      <Badge variant="outline">tools: inherit</Badge>
                    ) : subagent.tools.length === 0 ? (
                      <Badge variant="outline">tools: none</Badge>
                    ) : (
                      subagent.tools.map((tool) => (
                        <Badge key={tool} variant="secondary">
                          {tool}
                        </Badge>
                      ))
                    )}
                    {subagent.disallowed_tools?.map((tool) => (
                      <Badge key={`blocked-${tool}`} variant="outline">
                        blocked: {tool}
                      </Badge>
                    ))}
                  </div>

                  <div className="pt-4 border-t border-border text-xs text-muted-foreground font-mono space-y-1">
                    <div>{subagent.path}</div>
                    {!subagent.active && subagent.shadowed_by && (
                      <div>shadowed by: {subagent.shadowed_by}</div>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{form.id ? "Edit Subagent" : "Create Subagent"}</DialogTitle>
            <DialogDescription>
              Keep the description focused on when LimeBot should choose this helper. That description is what drives auto-selection.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="subagent-name">Name</Label>
              <Input
                id="subagent-name"
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="code-reviewer"
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="subagent-location">Location</Label>
              <Select
                value={form.location}
                onValueChange={(value) =>
                  setForm((prev) => ({ ...prev, location: value }))
                }
              >
                <SelectTrigger id="subagent-location">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(locationOptions.length > 0
                    ? locationOptions
                    : [
                        {
                          value: "project_limebot",
                          label: "Project (.limebot/agents)",
                          path: ".limebot/agents",
                        },
                        {
                          value: "project_claude",
                          label: "Project (.claude/agents)",
                          path: ".claude/agents",
                        },
                        {
                          value: "user_limebot",
                          label: "Personal (~/.limebot/agents)",
                          path: "~/.limebot/agents",
                        },
                        {
                          value: "user_claude",
                          label: "Personal (~/.claude/agents)",
                          path: "~/.claude/agents",
                        },
                      ]).map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="subagent-description">Description</Label>
              <Textarea
                id="subagent-description"
                value={form.description}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, description: e.target.value }))
                }
                className="min-h-[90px]"
                placeholder="Review code for bugs, regressions, and missing tests."
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="subagent-prompt">System Prompt</Label>
              <Textarea
                id="subagent-prompt"
                value={form.prompt}
                onChange={(e) => setForm((prev) => ({ ...prev, prompt: e.target.value }))}
                className="min-h-[180px] font-mono text-sm"
                placeholder="Focus on concrete findings first. Keep the report concise and actionable."
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="subagent-tools">Tools</Label>
                <Input
                  id="subagent-tools"
                  value={form.toolsText}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, toolsText: e.target.value }))
                  }
                  placeholder="Read, Grep, Bash"
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="subagent-disallowed-tools">Disallowed Tools</Label>
                <Input
                  id="subagent-disallowed-tools"
                  value={form.disallowedToolsText}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, disallowedToolsText: e.target.value }))
                  }
                  placeholder="Delete, Write"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="subagent-model">Model</Label>
                <Input
                  id="subagent-model"
                  value={form.model}
                  onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value }))}
                  placeholder="inherit"
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="subagent-max-turns">Max Turns</Label>
                <Input
                  id="subagent-max-turns"
                  value={form.maxTurns}
                  onChange={(e) => setForm((prev) => ({ ...prev, maxTurns: e.target.value }))}
                  placeholder="8"
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="subagent-background">Background</Label>
                <div className="flex h-10 items-center justify-between rounded-md border border-input bg-background px-3">
                  <span className="text-sm text-muted-foreground">
                    Return later
                  </span>
                  <Switch
                    id="subagent-background"
                    checked={form.background}
                    onCheckedChange={(checked) =>
                      setForm((prev) => ({ ...prev, background: checked }))
                    }
                  />
                </div>
              </div>
            </div>

          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button onClick={saveSubagent} disabled={saving}>
              {saving ? "Saving..." : form.id ? "Save Changes" : "Create Subagent"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
