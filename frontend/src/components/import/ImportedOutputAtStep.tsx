"use client";

/**
 * ImportedOutputAtStep — thin wrapper over shared OutputAtStep for
 * imported-experiment research appearance.
 */

import { OutputAtStep } from "@/components/output/OutputAtStep";
import type { Prov } from "@/types/provenance";
import { isUnavailable } from "@/types/provenance";

interface Props {
  prefixBefore: Prov<string>;
  selectedToken: string | null;
  selectedUnavailable: boolean;
  className?: string;
}

export function ImportedOutputAtStep({
  prefixBefore,
  selectedToken,
  selectedUnavailable,
  className,
}: Props) {
  const prefixOk = !isUnavailable(prefixBefore) && prefixBefore.value !== null;
  const prefixText = prefixOk ? String(prefixBefore.value) : null;

  return (
    <OutputAtStep
      prefixBefore={prefixText}
      selectedToken={selectedToken}
      selectedUnavailable={selectedUnavailable}
      className={className}
      appearance="research"
      title="Output at this step"
    />
  );
}
