"use client";

import * as React from "react";

/**
 * Cloudflare Turnstile widget - the privacy-friendly CAPTCHA that gates the waitlist form.
 *
 * Rendering is opt-in on configuration: when `NEXT_PUBLIC_TURNSTILE_SITE_KEY` is unset the
 * component renders nothing and verification is effectively off, so the form works in dev
 * with zero setup. When set, the widget produces a short-lived token that the parent sends
 * to the backend, which verifies it server-side.
 *
 * The script-loading dance (load once, explicit render, clean up on unmount) mirrors the
 * pattern already proven on the personal site's contact form.
 */

export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;

interface TurnstileApi {
  render: (
    element: string | HTMLElement,
    options: {
      sitekey: string;
      callback: (token: string) => void;
      "error-callback"?: () => void;
      "expired-callback"?: () => void;
      theme?: "light" | "dark" | "auto";
    },
  ) => string;
  reset: (widgetId: string) => void;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    __turnstileScriptLoaded?: boolean;
  }
}

/** Load the Turnstile script once globally (survives component re-mounts). */
function loadTurnstileScript(): Promise<void> {
  if (window.__turnstileScriptLoaded && window.turnstile) return Promise.resolve();
  const existing = document.querySelector(
    'script[src*="challenges.cloudflare.com/turnstile"]',
  );
  if (existing) {
    return new Promise((resolve) => {
      const check = () => {
        if (window.turnstile) {
          window.__turnstileScriptLoaded = true;
          resolve();
        } else setTimeout(check, 50);
      };
      check();
    });
  }
  return new Promise((resolve) => {
    const script = document.createElement("script");
    script.src =
      "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    script.async = true;
    script.onload = () => {
      window.__turnstileScriptLoaded = true;
      resolve();
    };
    document.head.appendChild(script);
  });
}

interface TurnstileProps {
  /** Called with a fresh token on success, and with `""` when it expires or errors. */
  onToken: (token: string) => void;
  className?: string;
  /** Bump this to force the widget to reset (e.g. after a submit). */
  resetSignal?: number;
}

export function Turnstile({ onToken, className, resetSignal }: TurnstileProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const widgetIdRef = React.useRef<string>("");
  // Keep the latest callback without re-running the render effect.
  const onTokenRef = React.useRef(onToken);
  onTokenRef.current = onToken;

  // Render exactly once on mount. `theme: "auto"` lets the widget follow the page's
  // color scheme itself, so we never remount it (and drop a solved token) on a theme change.
  React.useEffect(() => {
    if (!TURNSTILE_SITE_KEY) return;
    let cancelled = false;

    loadTurnstileScript().then(() => {
      if (cancelled || !containerRef.current || !window.turnstile) return;
      if (widgetIdRef.current) {
        window.turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = "";
      }
      requestAnimationFrame(() => {
        if (cancelled || !containerRef.current || !window.turnstile) return;
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: TURNSTILE_SITE_KEY!,
          callback: (token) => !cancelled && onTokenRef.current(token),
          "error-callback": () => !cancelled && onTokenRef.current(""),
          "expired-callback": () => !cancelled && onTokenRef.current(""),
          theme: "auto",
        });
      });
    });

    return () => {
      cancelled = true;
      if (widgetIdRef.current && window.turnstile) {
        window.turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = "";
      }
    };
  }, []);

  // Reset the widget on demand (a Turnstile token is single-use; after a submit we need a
  // fresh one for any retry).
  React.useEffect(() => {
    if (resetSignal && widgetIdRef.current && window.turnstile) {
      window.turnstile.reset(widgetIdRef.current);
      onTokenRef.current("");
    }
  }, [resetSignal]);

  if (!TURNSTILE_SITE_KEY) return null;
  return <div ref={containerRef} className={className} />;
}
