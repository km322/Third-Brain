/** Where the demo media lives. Named once so a re-encode or rename is a one-line change. */
export const DEMO_MEDIA = {
  poster: "/media/third-brain-demo-poster.webp",
  hd: "/media/third-brain-demo-1080p.mp4",
  sd: "/media/third-brain-demo-720p.mp4",
  /** Viewports at or above this width get the 1080p rendition. */
  hdMinWidth: "1024px",
} as const;

/**
 * Landing demo section: the recorded product walkthrough, framed like the hero's
 * proof element - hairline edge, one neutral shadow, a figure caption underneath
 * left-aligned to the frame edge as in the hero.
 *
 * Cheap by construction, because this sits four to six screens below the fold:
 * - `preload="none"` - the video is only fetched when a visitor presses play.
 * - Two renditions. The frame is at most 896 CSS px wide, so a phone displays it
 *   in roughly 1020 device px and decoding 1080p there would burn 2.25x the
 *   pixels (and 3x the bytes) for nothing. Narrow viewports get 720p; the 1080p
 *   rendition is reserved for widths that can actually resolve it.
 * - `muted` - the recording is captioned on screen and carries no audio track at
 *   all, so this is purely accurate. It also lets a backgrounded tab fall under
 *   the browser's aggressive throttling for silent video instead of holding an
 *   audio-focus slot.
 * - A WebP poster rather than the source JPEG: same frame, a third of the bytes,
 *   and it is the one raster asset every visitor pays for.
 */
export function DemoVideo() {
  return (
    <section id="demo" className="scroll-mt-20 py-28 sm:py-32 lg:py-40">
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl">
            Watch it write the doc.
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Three minutes against the running product: ingest a document, ask a grounded
            question, watch an agent capture a decision mid-chat - then watch the same
            question return nothing to someone who isn&apos;t allowed to see it.
          </p>
        </div>

        <div className="mx-auto mt-16 max-w-4xl">
          <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-[0_1px_1px_rgba(0,0,0,0.03),0_12px_32px_-8px_rgba(0,0,0,0.08)] ring-1 ring-black/[0.04] dark:shadow-none dark:ring-white/[0.08]">
            <video
              className="aspect-video w-full bg-black"
              controls
              muted
              playsInline
              preload="none"
              poster={DEMO_MEDIA.poster}
              aria-label="Third Brain product demo - a three-minute walkthrough"
            >
              <source
                src={DEMO_MEDIA.hd}
                type="video/mp4"
                media={`(min-width: ${DEMO_MEDIA.hdMinWidth})`}
              />
              <source src={DEMO_MEDIA.sd} type="video/mp4" />
            </video>
          </div>

          <p className="mt-4 flex items-baseline gap-2 text-sm text-muted-foreground">
            <span className="shrink-0 font-mono text-xs text-muted-foreground/70">
              Fig. 2
            </span>
            The whole loop end to end - capture, governed retrieval, and the knowledge
            graph the documents form on their own.
          </p>
        </div>
      </div>
    </section>
  );
}
