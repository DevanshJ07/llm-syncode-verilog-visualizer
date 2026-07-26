"use client";

/**
 * PromptForm — controlled input component.
 *
 * All generation state lives in the parent (page.tsx) via useGeneration.
 * This component only owns the prompt text, settings, and Syncode toggle.
 *
 * The Syncode toggle is functional.  When enabled, POST /generate applies
 * Verilog-grammar masking at each decoding step and populates
 * top_tokens_before_syncode / masked_tokens / valid_tokens_after_syncode.
 */

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { GenerationSettings } from "@/components/prompt/GenerationSettings";
import { DEFAULT_MAX_NEW_TOKENS } from "@/lib/generationDefaults";
import type { GenerateRequest } from "@/types/decoding";

interface Props {
  onSubmit: (req: GenerateRequest) => void;
  isLoading: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Preset prompts
// ---------------------------------------------------------------------------

const PRESETS: { label: string; prompt: string }[] = [
  {
    label: "Mux 2-to-1 (basic)",
    prompt: "Write a Verilog module that implements a 2-to-1 multiplexer with inputs a, b, sel and output y.",
  },
  {
    label: "Mux rewrite (strict)",
    prompt: `Generate only one Verilog module.
The output must start with:
module mux_cell(a, b, select, out);
Do not use mux2, sel, or y anywhere in the rewritten code.
Use select as the mux select signal.
Use out as the output signal.
Use only input, output, assign, and ternary operator.
Do not use always, reg, if, case, vectors, or arithmetic.

Original code:
module mux2(a, b, sel, y);
  input a, b, sel;
  output y;

  assign y = sel ? b : a;
endmodule

Expected output:
module mux_cell(a, b, select, out);
  input a, b, select;
  output out;

  assign out = select ? b : a;
endmodule`,
  },
  {
    label: "Full adder (grammar-valid)",
    prompt: `Generate only this Verilog module, no explanation, no markdown:

module fa_cell(a, b, carry_in, sum, carry_out);
  input a, b, carry_in;
  output sum, carry_out;

  assign sum = a ^ b ^ carry_in;
  assign carry_out = (a & b) | (b & carry_in) | (a & carry_in);
endmodule`,
  },
  {
    label: "Full adder (open)",
    prompt: "Write a Verilog module that implements a 1-bit full adder with inputs a, b, cin and outputs sum, cout.",
  },
  {
    label: "AND gate",
    prompt: "Write a Verilog module that implements a 2-input AND gate with inputs a, b and output y.",
  },
  {
    label: "D flip-flop",
    prompt: "Write a Verilog module that implements a D flip-flop using only assign and wire statements.",
  },
];

const DEFAULT_PROMPT = PRESETS[0].prompt;

export function PromptForm({ onSubmit, isLoading, error }: Props) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [useSyncode, setUseSyncode] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<Omit<GenerateRequest, "prompt" | "use_syncode">>({
    top_k: 20,
    max_new_tokens: DEFAULT_MAX_NEW_TOKENS,
    temperature: 1.0,
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({ prompt, use_syncode: useSyncode, ...settings });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {/* Preset selector */}
      <div className="flex flex-col gap-1.5">
        <label className="text-xs font-medium uppercase tracking-wider text-[#8b949e]">
          Presets
        </label>
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              disabled={isLoading}
              onClick={() => setPrompt(p.prompt)}
              className={`rounded border px-2 py-0.5 text-[11px] transition-colors disabled:opacity-40 ${
                prompt === p.prompt
                  ? "border-accent-blue bg-accent-blue/10 text-accent-blue"
                  : "border-surface-border text-[#8b949e] hover:border-accent-blue/50 hover:text-[#e6edf3]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Prompt */}
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="prompt"
          className="text-xs font-medium uppercase tracking-wider text-[#8b949e]"
        >
          Prompt
        </label>
        <textarea
          id="prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={5}
          disabled={isLoading}
          placeholder="Enter a Verilog prompt…"
          className="code-block w-full resize-y rounded-md border border-surface-border bg-surface p-3 text-[#e6edf3] placeholder-[#484f58] focus:border-accent-blue focus:outline-none focus:ring-1 focus:ring-accent-blue disabled:opacity-50"
        />
      </div>

      {/* Syncode toggle */}
      <label className="flex cursor-pointer items-center gap-3 rounded-md border border-surface-border bg-surface px-3 py-2 transition-colors hover:border-[#30363d]">
        <div className="relative shrink-0" onClick={() => !isLoading && setUseSyncode((v) => !v)}>
          <div
            className={`h-5 w-9 rounded-full transition-colors ${
              useSyncode ? "bg-accent-blue" : "bg-surface-border"
            }`}
          />
          <div
            className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
              useSyncode ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </div>
        <div className="flex flex-col gap-0.5">
          <span className={`text-sm ${useSyncode ? "text-accent-blue" : "text-[#8b949e]"}`}>
            Syncode constrained decoding
          </span>
          <span className="text-[10px] text-[#484f58]">
            {useSyncode
              ? "Grammar masking active — invalid Verilog tokens will be suppressed"
              : "Off — raw nucleus sampling, no grammar constraint"}
          </span>
        </div>
        {useSyncode && (
          <span className="ml-auto rounded border border-accent-blue/40 bg-accent-blue/10 px-1.5 py-0.5 text-[10px] text-accent-blue">
            Verilog grammar
          </span>
        )}
      </label>

      {/* Advanced settings */}
      <button
        type="button"
        disabled={isLoading}
        onClick={() => setShowSettings((v) => !v)}
        className="self-start text-xs text-[#8b949e] transition-colors hover:text-accent-blue disabled:opacity-40"
      >
        {showSettings ? "▾ Hide settings" : "▸ Advanced settings"}
      </button>

      {showSettings && (
        <GenerationSettings value={settings} onChange={setSettings} />
      )}

      {error && (
        <p className="rounded-md border border-accent-red/30 bg-red-900/20 px-3 py-2 text-sm text-accent-red">
          {error}
        </p>
      )}

      <p className="text-[11px] text-[#484f58]">
        Qwen2.5-Coder-1.5B · CPU
        {useSyncode && " · Syncode Verilog grammar (DFA builds on first run ~30 s)"}
        {!useSyncode && " · ~30–90 s on first run (model downloads once)"}
      </p>

      <Button type="submit" loading={isLoading} size="lg" className="self-start">
        {isLoading ? "Generating…" : "Generate"}
      </Button>
    </form>
  );
}
