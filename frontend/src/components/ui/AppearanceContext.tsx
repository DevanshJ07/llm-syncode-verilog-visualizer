"use client";

/**
 * Optional appearance context for Phase 5A.2.
 * Imported workspace provides "research"; live visualizer leaves default.
 */

import { createContext, useContext, type ReactNode } from "react";
import type { UiAppearance } from "@/lib/researchAppearance";

const AppearanceContext = createContext<UiAppearance>("default");

export function AppearanceProvider({
  appearance,
  children,
}: {
  appearance: UiAppearance;
  children: ReactNode;
}) {
  return (
    <AppearanceContext.Provider value={appearance}>
      {children}
    </AppearanceContext.Provider>
  );
}

/** Prefer explicit prop when provided; otherwise use ambient context. */
export function useUiAppearance(
  explicit?: UiAppearance
): UiAppearance {
  const ctx = useContext(AppearanceContext);
  return explicit ?? ctx;
}
