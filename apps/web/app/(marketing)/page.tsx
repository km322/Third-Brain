import { FeatureGrid } from "@/components/marketing/feature-grid";
import { Hero } from "@/components/marketing/hero";
import { HowItWorks } from "@/components/marketing/how-it-works";
import { Integrations } from "@/components/marketing/integrations";
import { SecuritySection } from "@/components/marketing/security-section";
import { Waitlist } from "@/components/marketing/waitlist";

/**
 * Third Brain marketing landing page. Composes the sections in narrative
 * order: what it is, what it does, how it works, what it connects to, how it's
 * secured, and the closing call to action.
 */
export default function LandingPage() {
  return (
    <>
      <Hero />
      <FeatureGrid />
      <HowItWorks />
      <Integrations />
      <SecuritySection />
      <Waitlist />
    </>
  );
}
