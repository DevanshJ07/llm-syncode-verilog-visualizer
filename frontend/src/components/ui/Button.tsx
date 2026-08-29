"use client";

import { cn } from "@/lib/utils";
import { ButtonHTMLAttributes, forwardRef } from "react";
import { useUiAppearance } from "@/components/ui/AppearanceContext";
import type { UiAppearance } from "@/lib/researchAppearance";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  appearance?: UiAppearance;
}

const darkVariant: Record<Variant, string> = {
  primary:
    "bg-accent-blue text-surface hover:bg-blue-400 focus-visible:ring-accent-blue",
  secondary:
    "bg-surface-raised border border-surface-border text-[#e6edf3] hover:border-[#484f58]",
  ghost: "text-[#8b949e] hover:text-[#e6edf3] hover:bg-surface-raised",
  danger:
    "bg-accent-red text-white hover:bg-red-400 focus-visible:ring-accent-red",
};

const researchVariant: Record<Variant, string> = {
  primary:
    "bg-blue-600 text-[#e5edf7] hover:bg-blue-500 focus-visible:ring-blue-400",
  secondary:
    "bg-[#172033] border border-[#334155] text-[#e5edf7] hover:bg-[#1e293b] hover:border-[#475569]",
  ghost: "text-[#a8b3c7] hover:text-[#e5edf7] hover:bg-[#172033]",
  danger:
    "bg-red-700/90 text-[#e5edf7] hover:bg-red-600 focus-visible:ring-red-400",
};

const sizeClasses: Record<Size, string> = {
  sm: "px-3 py-1.5 text-xs",
  md: "px-4 py-2 text-sm",
  lg: "px-6 py-3 text-base",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "primary",
      size = "md",
      loading = false,
      disabled,
      appearance: appearanceProp,
      className,
      children,
      ...props
    },
    ref
  ) => {
    const appearance = useUiAppearance(appearanceProp);
    const variants =
      appearance === "research" ? researchVariant : darkVariant;
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "inline-flex items-center justify-center gap-2 rounded-md font-medium",
          "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
          appearance === "research"
            ? "focus-visible:ring-offset-[#0b1120] disabled:opacity-60 disabled:pointer-events-none"
            : "focus-visible:ring-offset-surface disabled:pointer-events-none disabled:opacity-50",
          variants[variant],
          sizeClasses[size],
          className
        )}
        {...props}
      >
        {loading && (
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        )}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
