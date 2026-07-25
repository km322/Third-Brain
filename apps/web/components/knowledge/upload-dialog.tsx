"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileUp, Link2, Loader2, Type, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";
import type { DocumentItem, Visibility } from "@/lib/types";

type Mode = "text" | "url" | "file";

const INHERIT = "__inherit__";

const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "team", label: "Team" },
  { value: "org", label: "Organization" },
  { value: "public", label: "Public" },
];

interface UploadDialogProps {
  collectionId: string;
  /** Custom trigger; defaults to an "Add document" button. */
  trigger?: React.ReactNode;
  /** Called with the created document after a successful add. */
  onCreated?: (doc: DocumentItem) => void;
}

/**
 * Adds a document to a collection from one of three sources - inline text, a
 * remote URL, or a file upload - via `POST /documents/{text|url|upload}`. On
 * success it invalidates the document + collection caches so lists refresh and
 * status polling picks the new row up immediately.
 */
export function UploadDialog({
  collectionId,
  trigger,
  onCreated,
}: UploadDialogProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [mode, setMode] = React.useState<Mode>("text");

  // Text
  const [title, setTitle] = React.useState("");
  const [content, setContent] = React.useState("");
  // URL
  const [url, setUrl] = React.useState("");
  const [urlTitle, setUrlTitle] = React.useState("");
  // File
  const [file, setFile] = React.useState<File | null>(null);
  // Shared
  const [visibility, setVisibility] = React.useState<string>(INHERIT);

  function reset() {
    setTitle("");
    setContent("");
    setUrl("");
    setUrlTitle("");
    setFile(null);
    setVisibility(INHERIT);
    setMode("text");
  }

  const vis = visibility === INHERIT ? undefined : (visibility as Visibility);

  const mutation = useMutation<DocumentItem>({
    mutationFn: async () => {
      if (mode === "text") {
        return api.post<DocumentItem>("/documents/text", {
          collection_id: collectionId,
          title: title.trim(),
          content,
          visibility: vis,
        });
      }
      if (mode === "url") {
        return api.post<DocumentItem>("/documents/url", {
          collection_id: collectionId,
          url: url.trim(),
          title: urlTitle.trim() || undefined,
          visibility: vis,
        });
      }
      const form = new FormData();
      form.append("collection_id", collectionId);
      form.append("file", file as File);
      if (vis) form.append("visibility", vis);
      return api.upload<DocumentItem>("/documents/upload", form);
    },
    onSuccess: (doc) => {
      toast.success("Document added", {
        description: "Ingestion has started - it'll appear once indexed.",
      });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["collection", collectionId] });
      queryClient.invalidateQueries({ queryKey: ["collections"] });
      onCreated?.(doc);
      reset();
      setOpen(false);
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to add document",
      );
    },
  });

  const canSubmit =
    mode === "text"
      ? title.trim().length > 0 && content.trim().length > 0
      : mode === "url"
        ? /^https?:\/\/\S+/i.test(url.trim())
        : file !== null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        {trigger ?? (
          <Button>
            <Upload className="h-4 w-4" />
            Add document
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a document</DialogTitle>
          <DialogDescription>
            Paste text, import a URL, or upload a file. It will be chunked,
            embedded and made searchable automatically.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="text" className="gap-1.5">
              <Type className="h-3.5 w-3.5" /> Text
            </TabsTrigger>
            <TabsTrigger value="url" className="gap-1.5">
              <Link2 className="h-3.5 w-3.5" /> URL
            </TabsTrigger>
            <TabsTrigger value="file" className="gap-1.5">
              <FileUp className="h-3.5 w-3.5" /> File
            </TabsTrigger>
          </TabsList>

          <TabsContent value="text" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="doc-title">Title</Label>
              <Input
                id="doc-title"
                placeholder="Q3 planning notes"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={1024}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="doc-content">Content</Label>
              <Textarea
                id="doc-content"
                placeholder="Paste or write markdown / plain text…"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                className="min-h-[160px]"
              />
            </div>
          </TabsContent>

          <TabsContent value="url" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="doc-url">URL</Label>
              <Input
                id="doc-url"
                type="url"
                placeholder="https://example.com/article"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="doc-url-title">
                Title{" "}
                <span className="font-normal text-muted-foreground">
                  (optional)
                </span>
              </Label>
              <Input
                id="doc-url-title"
                placeholder="Derived from the page when left blank"
                value={urlTitle}
                onChange={(e) => setUrlTitle(e.target.value)}
                maxLength={1024}
              />
            </div>
          </TabsContent>

          <TabsContent value="file" className="pt-2">
            <label
              htmlFor="doc-file"
              className={cn(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-card/40 px-4 py-10 text-center transition-colors hover:border-primary/50 hover:bg-accent/30",
                file && "border-primary/50",
              )}
            >
              <span className="flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
                <FileUp className="h-5 w-5" />
              </span>
              {file ? (
                <div className="space-y-0.5">
                  <p className="text-sm font-medium text-foreground">
                    {file.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(file.size)}
                  </p>
                </div>
              ) : (
                <div className="space-y-0.5">
                  <p className="text-sm font-medium text-foreground">
                    Click to choose a file
                  </p>
                  <p className="text-xs text-muted-foreground">
                    PDF, Markdown, text, HTML, DOCX, images (PNG/JPEG/GIF/WebP) · up to 25 MB
                  </p>
                </div>
              )}
              <input
                id="doc-file"
                type="file"
                className="sr-only"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </TabsContent>
        </Tabs>

        <div className="space-y-1.5">
          <Label htmlFor="doc-visibility">Visibility</Label>
          <Select value={visibility} onValueChange={setVisibility}>
            <SelectTrigger id="doc-visibility">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={INHERIT}>Inherit from collection</SelectItem>
              {VISIBILITY_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!canSubmit || mutation.isPending}
          >
            {mutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Adding…
              </>
            ) : (
              "Add document"
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
