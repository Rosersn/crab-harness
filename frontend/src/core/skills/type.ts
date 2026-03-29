export interface Skill {
  name: string;
  description: string;
  category: string;
  license: string | null;
  enabled: boolean;
  bosKey?: string | null;
  source: "platform" | "custom";
}
