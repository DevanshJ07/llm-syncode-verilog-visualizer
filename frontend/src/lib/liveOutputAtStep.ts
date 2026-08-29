/**
 * Reconstruct output at a selected live decoding step from stored tokens only.
 * Matches imported-experiment semantics: prefix = tokens before the selection,
 * then the selected token is shown distinctly.
 */

import type { DecodingStep } from "@/types/decoding";

export interface LiveOutputAtStepParts {
  /** Concatenation of selected_token for steps [0, index). */
  prefixBefore: string | null;
  selectedToken: string | null;
  selectedUnavailable: boolean;
  /** Prefix + selected (when both available). */
  fullAtStep: string | null;
}

/**
 * @param steps live DecodingStep[] in generation order
 * @param index 0-based index into steps (not the recorded step.step field)
 */
export function reconstructLiveOutputAtStep(
  steps: DecodingStep[],
  index: number
): LiveOutputAtStepParts {
  if (index < 0 || index >= steps.length) {
    return {
      prefixBefore: null,
      selectedToken: null,
      selectedUnavailable: true,
      fullAtStep: null,
    };
  }

  const parts: string[] = [];
  for (let i = 0; i < index; i++) {
    const tok = steps[i]?.selected_token;
    if (tok === null || tok === undefined) {
      return {
        prefixBefore: null,
        selectedToken: null,
        selectedUnavailable: true,
        fullAtStep: null,
      };
    }
    parts.push(tok);
  }

  const prefixBefore = parts.join("");
  const selected = steps[index]?.selected_token;
  if (selected === null || selected === undefined) {
    return {
      prefixBefore,
      selectedToken: null,
      selectedUnavailable: true,
      fullAtStep: null,
    };
  }

  return {
    prefixBefore,
    selectedToken: selected,
    selectedUnavailable: false,
    fullAtStep: prefixBefore + selected,
  };
}

/** Escape for single-line display (newlines → \\n, etc.). */
export function escapeLiveTokenForDisplay(token: string): string {
  return JSON.stringify(token);
}
