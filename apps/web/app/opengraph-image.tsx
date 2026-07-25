import { ImageResponse } from "next/og";

import { LOGO_PATH } from "@/components/brand/logo";

// Link-preview (Open Graph / Twitter) card, so a shared Third Brain link renders a branded
// preview instead of a blank box.
export const alt = "Third Brain - the documentation writes itself";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          background: "linear-gradient(135deg, #0b0b12 0%, #1a1030 55%, #2a1a4a 100%)",
          color: "#ffffff",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div
            style={{
              width: 72,
              height: 72,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 16,
              background: "#18181B",
            }}
          >
            <svg width="46" height="46" viewBox="0 0 64 64">
              <path d={LOGO_PATH} fill="#ffffff" />
            </svg>
          </div>
          <div style={{ fontSize: 40, fontWeight: 700, letterSpacing: -1 }}>
            Third Brain
          </div>
        </div>
        <div
          style={{
            marginTop: 48,
            fontSize: 66,
            fontWeight: 800,
            lineHeight: 1.1,
            letterSpacing: -2,
            maxWidth: 900,
          }}
        >
          The documentation writes itself.
        </div>
        <div style={{ marginTop: 28, fontSize: 32, color: "#c9c2e0", maxWidth: 880 }}>
          A governed company brain your agents fill in as they work - searchable by every model.
        </div>
        <div
          style={{
            position: "absolute",
            bottom: 80,
            left: 80,
            fontSize: 26,
            letterSpacing: 1,
            color: "#8a82a8",
          }}
        >
          third-brain.ai
        </div>
      </div>
    ),
    { ...size },
  );
}
