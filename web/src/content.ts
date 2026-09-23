import {
  AlertTriangle,
  BarChart3,
  Beaker,
  BookOpen,
  Calculator,
  FlaskConical,
  Layers,
  Rocket,
  ShieldCheck,
  Table2,
  Thermometer,
  type LucideIcon,
} from "lucide-react";

/** An example prompt; `needs` is the slash command a card applies first (literature tools are off by default),
 * `feature` what it relies on that a small deployment may not offer. */
export type Example = { text: string; needs?: string; feature?: "literature" | "tea" };
export type QuickAction = { label: string; blurb: string; icon: LucideIcon; examples: Example[]; feature?: "literature" | "tea" };

export const QUICK_ACTIONS: QuickAction[] = [
  {
    label: "Polymer Dissolution",
    blurb: "Which solvents dissolve a polymer, and at what temperature",
    icon: Beaker,
    examples: [
      { text: "Which solvents dissolve PS at 100 °C? Include boiling points and safety scores." },
      { text: "Find solvents that dissolve EVOH at 120 °C while keeping LDPE insoluble, then rank them by safety." },
      { text: "Screen PET dissolution solvents below 140 °C and summarize the top candidates by hazard score and boiling point." },
      { text: "Is LDPE soluble in dodecane at 110 °C? Show the solubility data behind the answer." },
    ],
  },
  {
    label: "Separation Planning",
    blurb: "Sequences, temperatures and precipitation order",
    icon: Layers,
    examples: [
      { text: "Design a separation sequence for LDPE/EVOH/PET multilayer film and justify each solvent choice." },
      { text: "Find the optimal separation order and temperatures for HDPE, PP and PS mixed waste." },
      { text: "Plan selective dissolution for EVOH/LDPE packaging and flag any narrow boiling-point margins at 1 atm." },
      { text: "In what order do PS and PMMA precipitate from toluene when the solution is cooled?" },
    ],
  },
  {
    label: "Solvent Safety",
    blurb: "Hazard scores, safety cards and greener substitutes",
    icon: ShieldCheck,
    examples: [
      { text: "Compare the safety of acetone, ethyl acetate, THF and methyl acetate at 60 °C." },
      { text: "Suggest greener substitutes for DMF when dissolving EVOH." },
      { text: "Show the safety card for toluene, including its hazard score and exposure limits." },
      { text: "Find green solvent candidates that dissolve PS below 110 °C." },
    ],
  },
  {
    label: "TEA + LCA",
    blurb: "Minimum selling price, GWP and sensitivity from live BioSTEAM",
    icon: Calculator,
    feature: "tea",
    examples: [
      { text: "What are the MSP and GWP for recovering LDPE with dodecane at 20,000 t/yr under energy case C1?" },
      { text: "Compare energy cases C1, C2 and C3 for LDPE recovery with dodecane." },
      { text: "How sensitive is the MSP of LDPE recovery with dodecane to the solvent price?" },
      { text: "Rank the stored campaign's LDPE process rows by MSP and GWP and show the Pareto front." },
    ],
  },
  {
    label: "Hansen & Thermal",
    blurb: "Solubility parameters, interaction spheres and Tg",
    icon: Thermometer,
    examples: [
      { text: "What are the Hansen solubility parameters of PVDF, and which solvents fall inside its interaction sphere?" },
      { text: "Screen the Hansen compatibility of PET with DMSO, NMP and benzyl alcohol." },
      { text: "What is the glass transition temperature of PET, and what evidence supports it?" },
      { text: "Compare the Hansen distances of toluene and xylene to polystyrene." },
    ],
  },
  {
    label: "Contaminant Removal",
    blurb: "Additive leaching washes and dissolution-based removal",
    icon: AlertTriangle,
    examples: [
      { text: "Which wash solvents remove DEHP from LDPE without dissolving it?" },
      { text: "Screen leaching of DEHP and DBP from PVC into ethanol and isopropanol." },
      { text: "For HDPE containing bisphenol A, compare removal by washing with removal by dissolution and reprecipitation." },
      {
        text: "Plan an LDPE/EVOH separation that also removes DEHP by washing against the remaining polymers.",
        needs: "/contaminant leaching",
      },
    ],
  },
  {
    label: "Research + RAG",
    blurb: "The pinned literature corpus, papers and patents",
    icon: BookOpen,
    feature: "literature",
    examples: [
      { text: "What does the literature corpus report about selective dissolution of EVOH from multilayer films?", needs: "/literature corpus" },
      { text: "Search the corpus for dissolution temperatures of LDPE in xylene.", needs: "/literature corpus" },
      { text: "Summarize published solvent systems for separating PET from PE, with citations.", needs: "/literature corpus" },
      { text: "Find recent papers and patents on solvent-targeted recovery of multilayer packaging.", needs: "/literature scholarly" },
    ],
  },
  {
    label: "Integrated Workflow",
    blurb: "Plan, check safety and cost in one request",
    icon: Rocket,
    examples: [
      { text: "For LDPE/EVOH/PET multilayer film: plan the separation, check each solvent's safety, and cost the LDPE stage.", feature: "tea" },
      { text: "I have mixed PE, PS and PET waste. Plan a separation sequence, suggest greener solvents where possible, and summarize the tradeoffs." },
      {
        text: "Plan an HDPE/EVOH separation with a DEHP wash step, then compare its solvent safety with a dissolution-based removal route.",
        needs: "/contaminant leaching",
      },
      { text: "Screen solvents for EVOH at 120 °C, check the literature for precedent, and recommend one.", needs: "/literature corpus" },
    ],
  },
];

export type Family = { label: string; icon: LucideIcon; color: string; tint: string };

const FAMILIES: Record<string, Family> = {
  separation: { label: "Solubility & separation", icon: Layers, color: "#3b82f6", tint: "rgba(59, 130, 246, 0.12)" },
  safety: { label: "Safety", icon: ShieldCheck, color: "#d97706", tint: "rgba(217, 119, 6, 0.12)" },
  tea: { label: "TEA + LCA", icon: Calculator, color: "#059669", tint: "rgba(5, 150, 105, 0.12)" },
  hansen: { label: "Hansen & thermal", icon: FlaskConical, color: "#0891b2", tint: "rgba(8, 145, 178, 0.12)" },
  contaminants: { label: "Contaminants", icon: AlertTriangle, color: "#e11d48", tint: "rgba(225, 29, 72, 0.12)" },
  literature: { label: "Literature", icon: BookOpen, color: "#7c3aed", tint: "rgba(124, 58, 237, 0.12)" },
  statistics: { label: "Statistics", icon: BarChart3, color: "#64748b", tint: "rgba(100, 116, 139, 0.12)" },
  results: { label: "Stored results", icon: Table2, color: "#64748b", tint: "rgba(100, 116, 139, 0.12)" },
};

const FAMILY_OF: Record<string, keyof typeof FAMILIES> = {
  solubility_query: "separation",
  screen_polymer_separation: "separation",
  screen_pairwise_solubility_overlap: "separation",
  resolve_polymer_data_scope: "separation",
  lookup_material_database_membership: "separation",
  plan_multistage_separation: "separation",
  screen_precipitation_order: "separation",
  screen_cool_then_reheat_getter: "separation",
  get_solvent_safety_card: "safety",
  compare_solvent_safety_at_conditions: "safety",
  screen_green_solvent_candidates: "safety",
  screen_route_solvent_substitutions: "safety",
  fetch_solvent_safety_by_cid: "safety",
  evaluate_process: "tea",
  rank_landscape: "tea",
  lookup_hansen_parameters: "hansen",
  screen_hansen_compatibility: "hansen",
  lookup_glass_transition: "hansen",
  list_thermal_evidence: "hansen",
  analyze_numeric_samples: "statistics",
  screen_contaminant_leaching: "contaminants",
  screen_contaminant_strap_removal: "contaminants",
  compare_contaminant_removal_modes: "contaminants",
  search_scholarly_literature: "literature",
  search_patent_literature: "literature",
  ingest_literature_documents: "literature",
  search_literature_corpus: "literature",
  inspect_literature_corpus: "literature",
  ingest_literature_graph: "literature",
  result_read: "results",
};

export const family = (tool: string): Family => FAMILIES[FAMILY_OF[tool] ?? "results"];

/** The cards and examples this deployment can answer. */
export function offeredActions(features: { literature: boolean; tea: boolean }): QuickAction[] {
  const offered = (feature?: "literature" | "tea") => !feature || features[feature];
  return QUICK_ACTIONS.filter((action) => offered(action.feature))
    .map((action) => ({
      ...action,
      examples: action.examples.filter((e) => offered(e.feature ?? (e.needs?.startsWith("/literature") ? "literature" : undefined))),
    }))
    .filter((action) => action.examples.length > 0);
}

/** The session modes the composer shows, in order, with how each reads on its chip. */
export const MODE_CHIPS = [
  { command: "/contaminant", state: "contaminant", label: "Contaminant" },
  { command: "/literature", state: "literature", label: "Literature" },
  { command: "/solvents", state: "solvents", label: "Solvents" },
  { command: "/breadth", state: "breadth", label: "Breadth" },
  { command: "/mode", state: "mode", label: "Assumptions" },
] as const;
