"use client";

import Link from "next/link";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";

import { Section } from "../section";

export function CommunitySection() {
  return (
    <Section
      title={
        <AuroraText colors={["#60A5FA", "#A5FA60", "#A560FA"]}>
          Get Started
        </AuroraText>
      }
      subtitle="Experience the power of Crab. Create custom skills, connect your tools, and let the AI agent handle the rest."
    >
      <div className="flex justify-center">
        <Button className="text-xl" size="lg" asChild>
          <Link href="/login">
            Start Now
          </Link>
        </Button>
      </div>
    </Section>
  );
}
