import type { MissionCard } from "@/api/generated/model";

export interface SelectedMissionCard {
  source_repo: string;
  kind: MissionCard["kind"];
  number: number;
  title: string | null;
}
