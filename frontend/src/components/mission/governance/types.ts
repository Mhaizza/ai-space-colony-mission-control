import type { MissionCard } from "@/api/generated/model";

/**
 * Minimal identity of a Mission card selected for the governance drawer.
 * Shared between `page.tsx` (owns the selection state) and
 * `MissionGovernanceDrawer` (renders it), so both sides agree on shape.
 */
export interface SelectedMissionCard {
  source_repo: string;
  kind: MissionCard["kind"];
  number: number;
  title: string | null;
}
