import type { Assessment, StructureCheck } from "./types";

export const technicalLabel = (assessment?: Assessment | null) =>
  !assessment ? "Not run" : assessment.technical_status === "passed" ? "Passed" : "Needs attention";

export const structureLabel = (structure?: StructureCheck | null) =>
  !structure || structure.status === "unavailable"
    ? "Unavailable"
    : structure.status === "passed"
      ? "Passed"
      : "Needs attention";
