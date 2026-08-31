import { Cta } from "@/components/marketing/cta";
import { DemoVideo } from "@/components/marketing/demo-video";
import { FeatureGrid } from "@/components/marketing/feature-grid";
import { Hero } from "@/components/marketing/hero";
import { HowItWorks } from "@/components/marketing/how-it-works";
import { Integrations } from "@/components/marketing/integrations";
import { SecuritySection } from "@/components/marketing/security-section";

/**
 * Third Brain marketing landing page. Composes the sections in narrative
 * order: what it is, what it does, how it works, what it looks like in motion,
 * what it connects to, how it's secured, and the closing call to action - the
 * same self-host on-ramp that closes the docs page.
 */
export default function LandingPage() {
  return (
    <>
      <Hero />
      <FeatureGrid />
      <HowItWorks />
      <DemoVideo />
      <Integrations />
      <SecuritySection />
      <Cta />
    </>
  );
}
