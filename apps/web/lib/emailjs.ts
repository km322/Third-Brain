// Best-effort email notification via EmailJS's REST API.
//
// This reuses the same EmailJS service/template/public key already configured for the
// personal site (https://www.emailjs.com), so no SDK dependency is needed - we POST the
// documented browser endpoint directly. Notifications are advisory: the durable record of
// a waitlist signup is the row persisted by the backend, so a failure here never blocks a
// signup. When the env vars are unset (dev/CI), sending is skipped and reported as `false`.

const EMAILJS_ENDPOINT = "https://api.emailjs.com/api/v1.0/email/send";

const SERVICE_ID = process.env.NEXT_PUBLIC_EMAILJS_SERVICE_ID;
const PUBLIC_KEY = process.env.NEXT_PUBLIC_EMAILJS_PUBLIC_KEY;
// A dedicated waitlist template can be supplied; otherwise fall back to the generic one.
const TEMPLATE_ID =
  process.env.NEXT_PUBLIC_EMAILJS_WAITLIST_TEMPLATE_ID ||
  process.env.NEXT_PUBLIC_EMAILJS_TEMPLATE_ID;

/** Whether EmailJS is configured. When false, {@link sendWaitlistNotification} is a no-op. */
export const emailNotificationsEnabled = Boolean(
  SERVICE_ID && TEMPLATE_ID && PUBLIC_KEY,
);

interface WaitlistNotification {
  email: string;
  name?: string;
  company?: string;
  source?: string;
}

/**
 * Notify the operator that someone joined the waitlist. Resolves to whether the email was
 * accepted by EmailJS. Never throws - callers treat it as fire-and-forget.
 *
 * The `template_params` intentionally mirror the existing contact template
 * (`from_name` / `from_email` / `message` / `to_name`) so it works with no template changes.
 */
export async function sendWaitlistNotification({
  email,
  name,
  company,
  source,
}: WaitlistNotification): Promise<boolean> {
  if (!emailNotificationsEnabled) return false;

  const details = [
    company ? `Company: ${company}` : null,
    source ? `Source: ${source}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  try {
    const res = await fetch(EMAILJS_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        service_id: SERVICE_ID,
        template_id: TEMPLATE_ID,
        user_id: PUBLIC_KEY,
        template_params: {
          from_name: name || email,
          from_email: email,
          to_name: "Third Brain",
          message: `New Third Brain waitlist signup: ${email}${details ? `\n${details}` : ""}`,
        },
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}
