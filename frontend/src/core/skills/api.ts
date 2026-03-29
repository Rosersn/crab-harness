import { getBackendBaseURL } from "@/core/config";
import { fetchJson } from "@/core/http/fetch";

import type { Skill } from "./type";

interface PlatformSkillResponse {
  name: string;
  description: string;
  category: string;
  license: string | null;
  enabled: boolean;
}

interface UserSkillConfigResponse {
  skill_name: string;
  enabled: boolean;
  bos_key: string | null;
}

interface UserSkillListResponse {
  platform_skills: PlatformSkillResponse[];
  user_configs: UserSkillConfigResponse[];
}

export async function loadSkills() {
  const data = await fetchJson<UserSkillListResponse>(
    `${getBackendBaseURL()}/api/skills/user/configs`,
    undefined,
    {
      fallbackMessage: "Failed to load skills",
    },
  );

  const userConfigMap = new Map(
    data.user_configs.map((config) => [config.skill_name, config]),
  );

  const platformSkills: Skill[] = data.platform_skills.map((skill) => ({
    name: skill.name,
    description: skill.description,
    category: skill.category,
    license: skill.license,
    enabled: userConfigMap.get(skill.name)?.enabled ?? skill.enabled,
    bosKey: userConfigMap.get(skill.name)?.bos_key,
    source: "platform",
  }));

  const customSkills: Skill[] = data.user_configs
    .filter(
      (config) =>
        !data.platform_skills.some((skill) => skill.name === config.skill_name),
    )
    .map((config) => ({
      name: config.skill_name,
      description: "Tenant-scoped custom skill",
      category: "custom",
      license: null,
      enabled: config.enabled,
      bosKey: config.bos_key,
      source: "custom",
    }));

  return [...platformSkills, ...customSkills];
}

export async function enableSkill(skillName: string, enabled: boolean) {
  return fetchJson<{ skill_name: string; enabled: boolean; bos_key: string | null }>(
    `${getBackendBaseURL()}/api/skills/user/configs/${skillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled,
      }),
    },
    {
      fallbackMessage: `Failed to update skill '${skillName}'`,
    },
  );
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  return {
    success: false,
    skill_name: request.path,
    message:
      "Browser-based custom skill installation is not available in this deployment yet.",
  };
}
