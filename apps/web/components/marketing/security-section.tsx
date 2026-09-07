interface Control {
  title: string;
  description: string;
}

const CONTROLS: Control[] = [
  {
    title: "Document-level RBAC",
    description:
      "Owner, admin, editor and viewer roles plus per-resource grants for users and teams.",
  },
  {
    title: "Org isolation",
    description:
      "Strict tenant scoping on every query - data never crosses organization boundaries.",
  },
  {
    title: "Full audit trail",
    description:
      "Every search, ingest, agent write-back and admin action is recorded with actor, IP and timestamp; per-key rate limits and usage metering included.",
  },
  {
    title: "Scoped API keys",
    description:
      "Least-privilege keys with read/write scopes, per-minute rate limits and act-as-user delegation; device-code sign-in mints them admin-approved.",
  },
  {
    title: "SSO & SCIM",
    description:
      "SAML 2.0 and OIDC sign-in with just-in-time provisioning; SCIM 2.0 provisions and deactivates users, and identity-provider groups arrive as teams.",
  },
  {
    title: "Encrypted secrets",
    description:
      "Connector credentials are encrypted at rest; API-key secrets are shown once, then hashed.",
  },
  {
    title: "Self-hosted",
    description:
      "You run Third Brain on your own infrastructure with your own provider keys, so your documents, embeddings and queries never leave your perimeter.",
  },
];

export function SecuritySection() {
  return (
    <section id="security" className="scroll-mt-20 py-28 sm:py-32 lg:py-40">
      <div className="container">
        <div className="mx-auto grid max-w-5xl gap-16 lg:grid-cols-2">
          {/* Left: narrative */}
          <div>
            <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl">
              Governed by default.
            </h2>
            <p className="mt-6 max-w-md text-pretty text-lg leading-relaxed text-muted-foreground">
              Third Brain treats access control as a first-class primitive, not an
              afterthought. Permissions are enforced at retrieval time, so a model only
              ever grounds its answers in data the caller is cleared to read - and the
              same gate governs what agents write back, while a secret scanner keeps
              credentials out of the brain.
            </p>
            <p className="mt-8 text-xs text-muted-foreground">
              Apache-2.0 licensed, so the enforcement path is yours to read, audit and
              change.
            </p>
          </div>

          {/* Right: the controls as a ledger */}
          <div className="divide-y divide-border/60">
            {CONTROLS.map((control) => (
              <div key={control.title} className="py-4 first:pt-0 last:pb-0">
                <h3 className="text-[15px] font-medium text-foreground">
                  {control.title}
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {control.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
