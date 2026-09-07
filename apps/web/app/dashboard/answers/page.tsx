"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, MessageSquareQuote, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { CardGridSkeleton } from "@/components/dashboard/loading";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { orgRoleAtLeast } from "@/components/governance/role-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Answer, Collection, VerificationStatus, Visibility } from "@/lib/types";
import { cn } from "@/lib/utils";

const VISIBILITIES: Visibility[] = ["private", "team", "org", "public"];
const NO_COLLECTION = "none";

const VERIFICATION_VARIANT: Record<
  VerificationStatus,
  "success" | "destructive" | "muted"
> = {
  verified: "success",
  stale: "destructive",
  unverified: "muted",
};

const VERIFICATION_LABEL: Record<VerificationStatus, string> = {
  verified: "Verified",
  stale: "Stale",
  unverified: "Unverified",
};

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function VerificationBadge({ status }: { status: VerificationStatus }) {
  return (
    <Badge variant={VERIFICATION_VARIANT[status]}>
      {status === "verified" ? <BadgeCheck className="mr-1 h-3 w-3" /> : null}
      {VERIFICATION_LABEL[status]}
    </Badge>
  );
}

export default function AnswersPage() {
  const { role } = useAuth();
  const canEdit = orgRoleAtLeast(role, "editor");
  const queryClient = useQueryClient();

  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Answer | null>(null);
  const [deleting, setDeleting] = React.useState<Answer | null>(null);

  const answersQuery = useQuery<Answer[]>({
    queryKey: ["answers"],
    queryFn: () => api.get<Answer[]>("/answers"),
  });

  const collectionsQuery = useQuery<Collection[]>({
    queryKey: ["collections"],
    queryFn: () => api.get<Collection[]>("/collections"),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["answers"] });

  const verifyAnswer = useMutation({
    mutationFn: (id: string) => api.post<Answer>(`/answers/${id}/verify`, {}),
    onSuccess: () => {
      toast.success("Answer verified");
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't verify answer")),
  });

  const deleteAnswer = useMutation({
    mutationFn: (id: string) => api.delete(`/answers/${id}`),
    onSuccess: () => {
      toast.success("Answer deleted");
      setDeleting(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't delete answer")),
  });

  const answers = answersQuery.data ?? [];
  const collections = collectionsQuery.data ?? [];
  const collectionName = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const c of collectionsQuery.data ?? []) map.set(c.id, c.name);
    return map;
  }, [collectionsQuery.data]);

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }
  function openEdit(a: Answer) {
    setEditing(a);
    setFormOpen(true);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Answers"
        description="Curated, verifiable Q&A that is surfaced above search so teams get authoritative answers, not just matching passages."
        actions={
          canEdit ? (
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" />
              New answer
            </Button>
          ) : null
        }
      />

      {answersQuery.isLoading ? (
        <CardGridSkeleton count={3} />
      ) : answersQuery.isError ? (
        <EmptyState
          icon={MessageSquareQuote}
          title="Couldn't load answers"
          description="Something went wrong fetching curated answers. Try again in a moment."
        />
      ) : answers.length === 0 ? (
        <EmptyState
          icon={MessageSquareQuote}
          title="No curated answers yet"
          description="Capture an authoritative answer to a common question so it shows above search results and can be verified on a schedule."
          actions={
            canEdit ? (
              <Button onClick={openCreate}>
                <Plus className="h-4 w-4" />
                New answer
              </Button>
            ) : null
          }
        />
      ) : (
        <div className="space-y-3">
          {answers.map((a) => {
            const verifying = verifyAnswer.isPending && verifyAnswer.variables === a.id;
            return (
              <Card key={a.id} className="space-y-3 p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 space-y-1">
                    <p className="font-semibold leading-snug">{a.question}</p>
                    <p className="line-clamp-2 whitespace-pre-wrap text-sm text-muted-foreground">
                      {a.answer}
                    </p>
                  </div>
                  <VerificationBadge status={a.verification_status} />
                </div>

                <Separator />

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span>
                      {a.collection_id
                        ? (collectionName.get(a.collection_id) ?? "Collection")
                        : "Org-wide"}
                    </span>
                    <span className="capitalize">{a.visibility}</span>
                    {a.verification_status === "verified" && a.verified_at ? (
                      <span>
                        Reviewed {fmtDate(a.verified_at)}
                        {a.expires_at ? ` · review by ${fmtDate(a.expires_at)}` : ""}
                      </span>
                    ) : a.verification_status === "stale" ? (
                      <span>Needs re-review</span>
                    ) : (
                      <span>Not yet verified</span>
                    )}
                  </div>

                  <div className="flex items-center gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={verifying}
                      onClick={() => verifyAnswer.mutate(a.id)}
                    >
                      <BadgeCheck className="h-4 w-4" />
                      {verifying
                        ? "Verifying…"
                        : a.verification_status === "unverified"
                          ? "Verify"
                          : "Re-verify"}
                    </Button>
                    {canEdit ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Edit answer"
                          onClick={() => openEdit(a)}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Delete answer"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setDeleting(a)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </>
                    ) : null}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {canEdit ? (
        <AnswerFormDialog
          open={formOpen}
          onOpenChange={setFormOpen}
          answer={editing ?? undefined}
          collections={collections}
          onSaved={invalidate}
        />
      ) : null}

      <Dialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete answer?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{deleting?.question}</span> will be removed
              and will no longer surface above search.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleting(null)}
              disabled={deleteAnswer.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteAnswer.isPending}
              onClick={() => deleting && deleteAnswer.mutate(deleting.id)}
            >
              {deleteAnswer.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function AnswerFormDialog({
  open,
  onOpenChange,
  answer,
  collections,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  answer?: Answer;
  collections: Collection[];
  onSaved: () => void;
}) {
  const editing = Boolean(answer);
  const [question, setQuestion] = React.useState("");
  const [answerText, setAnswerText] = React.useState("");
  const [visibility, setVisibility] = React.useState<Visibility>("org");
  const [collectionId, setCollectionId] = React.useState<string>(NO_COLLECTION);

  React.useEffect(() => {
    if (!open) return;
    setQuestion(answer?.question ?? "");
    setAnswerText(answer?.answer ?? "");
    setVisibility(answer?.visibility ?? "org");
    setCollectionId(answer?.collection_id ?? NO_COLLECTION);
  }, [open, answer]);

  const save = useMutation({
    mutationFn: () => {
      if (editing) {
        return api.patch<Answer>(`/answers/${answer!.id}`, {
          question: question.trim(),
          answer: answerText.trim(),
          visibility,
        });
      }
      return api.post<Answer>("/answers", {
        question: question.trim(),
        answer: answerText.trim(),
        visibility,
        collection_id: collectionId === NO_COLLECTION ? null : collectionId,
      });
    },
    onSuccess: () => {
      toast.success(editing ? "Answer updated" : "Answer created");
      onOpenChange(false);
      onSaved();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't save answer")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || !answerText.trim()) return;
    save.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit answer" : "New answer"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Editing the question or answer resets verification to unverified until it is reviewed again."
                : "Curated answers are shown above search results for matching questions."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="a-question">Question</Label>
              <Input
                id="a-question"
                required
                placeholder="What is our incident response process?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="a-answer">Answer</Label>
              <Textarea
                id="a-answer"
                required
                className="min-h-[140px]"
                placeholder="Write the authoritative answer teams should rely on."
                value={answerText}
                onChange={(e) => setAnswerText(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="a-visibility">Visibility</Label>
                <Select
                  value={visibility}
                  onValueChange={(v) => setVisibility(v as Visibility)}
                >
                  <SelectTrigger id="a-visibility" className="capitalize">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {VISIBILITIES.map((v) => (
                      <SelectItem key={v} value={v} className="capitalize">
                        {v}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {!editing ? (
                <div className="space-y-1.5">
                  <Label htmlFor="a-collection">Collection</Label>
                  <Select value={collectionId} onValueChange={setCollectionId}>
                    <SelectTrigger id="a-collection">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_COLLECTION}>
                        Org-wide (no collection)
                      </SelectItem>
                      {collections.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
            </div>
            {!editing ? (
              <p className="text-xs text-muted-foreground">
                Scoping to a collection lets its editors manage the answer and its
                managers verify it; org-wide answers are governed by their visibility.
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={save.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={save.isPending || !question.trim() || !answerText.trim()}
              className={cn(save.isPending && "opacity-80")}
            >
              {save.isPending ? "Saving…" : editing ? "Save changes" : "Create answer"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
