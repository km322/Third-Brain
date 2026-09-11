"use client";

import * as React from "react";

/**
 * A small, dependency-free markdown renderer that emits React elements only -
 * never HTML strings - so untrusted document content can't inject markup.
 *
 * Supports the subset that matters for knowledge documents: headings, paragraphs,
 * bold / italic / inline code, fenced code blocks, links (http/https only),
 * unordered + ordered lists, blockquotes and horizontal rules. Everything else
 * renders as plain text, which is the safe default.
 */

/**
 * Inline span tokens: bold, italic, inline code and links. Quantifiers are bounded
 * so a pathological line (many "[" with no closing "]") cannot backtrack
 * quadratically and freeze the tab while typing.
 */
const INLINE_RE =
  /(\*\*[^*]{1,500}\*\*|\*[^*\n]{1,500}\*|`[^`\n]{1,500}`|\[[^\]\n]{1,500}\]\([^)\s]{1,2000}\))/g;

function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(INLINE_RE)) {
    const index = match.index ?? 0;
    if (index > last) nodes.push(text.slice(last, index));
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key++}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={key++}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      const label = token.slice(1, token.indexOf("]"));
      const href = token.slice(token.indexOf("(") + 1, -1);
      if (/^https?:\/\//i.test(href)) {
        nodes.push(
          <a
            key={key++}
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-primary underline underline-offset-2"
          >
            {label}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }
    last = index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const HEADING_CLASSES: Record<number, string> = {
  1: "mt-6 text-xl font-semibold first:mt-0",
  2: "mt-5 text-lg font-semibold first:mt-0",
  3: "mt-4 text-base font-semibold first:mt-0",
  4: "mt-3 text-sm font-semibold first:mt-0",
  5: "mt-3 text-sm font-semibold text-muted-foreground first:mt-0",
  6: "mt-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground first:mt-0",
};

interface Block {
  key: number;
  node: React.ReactNode;
}

/**
 * Split `text` into block-level nodes, testing each line against the block kinds
 * in turn: fenced code block (whose closing fence, or EOF, is consumed with it),
 * heading, horizontal rule, blockquote (consecutive `>` lines), unordered list,
 * ordered list, and finally a paragraph - consecutive plain lines joined with
 * soft breaks.
 */
function parseBlocks(text: string): Block[] {
  const lines = text.replaceAll("\r\n", "\n").split("\n");
  const blocks: Block[] = [];
  let key = 0;
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (line.trimStart().startsWith("```")) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push({
        key: key++,
        node: (
          <pre className="my-3 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs leading-relaxed">
            {body.join("\n")}
          </pre>
        ),
      });
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${level}` as keyof React.JSX.IntrinsicElements;
      blocks.push({
        key: key++,
        node: <Tag className={HEADING_CLASSES[level]}>{renderInline(heading[2])}</Tag>,
      });
      i += 1;
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ key: key++, node: <hr className="my-4 border-border" /> });
      i += 1;
      continue;
    }

    if (line.trimStart().startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith(">")) {
        body.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      blocks.push({
        key: key++,
        node: (
          <blockquote className="my-3 border-l-2 border-border pl-3 text-muted-foreground">
            {renderInline(body.join(" "))}
          </blockquote>
        ),
      });
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({
        key: key++,
        node: (
          <ul className="my-2 list-disc space-y-1 pl-5">
            {items.map((item, n) => (
              <li key={n}>{renderInline(item)}</li>
            ))}
          </ul>
        ),
      });
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i += 1;
      }
      blocks.push({
        key: key++,
        node: (
          <ol className="my-2 list-decimal space-y-1 pl-5">
            {items.map((item, n) => (
              <li key={n}>{renderInline(item)}</li>
            ))}
          </ol>
        ),
      });
      continue;
    }

    const body: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !lines[i].trimStart().startsWith("```") &&
      !lines[i].trimStart().startsWith(">") &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i])
    ) {
      body.push(lines[i]);
      i += 1;
    }
    blocks.push({
      key: key++,
      node: (
        <p className="my-2 leading-relaxed">
          {body.map((text, n) => (
            <React.Fragment key={n}>
              {n > 0 ? <br /> : null}
              {renderInline(text)}
            </React.Fragment>
          ))}
        </p>
      ),
    });
  }

  return blocks;
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = React.useMemo(() => parseBlocks(text), [text]);
  return (
    <div className={className ?? "text-sm text-foreground"}>
      {blocks.map((block) => (
        <React.Fragment key={block.key}>{block.node}</React.Fragment>
      ))}
    </div>
  );
}
