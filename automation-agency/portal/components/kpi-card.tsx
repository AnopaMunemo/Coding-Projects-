"use client";

import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

/**
 * The house easing curve. Used on every transition in the portal.
 *
 * Framework defaults are a tell — they are what an AI-generated template
 * ships with. One consistent custom curve across every surface is most of
 * what "premium" actually means in practice.
 */
export const EASE = [0.22, 1, 0.36, 1] as const;

type Props = {
  label: string;
  value: string;
  caption?: string;
  delta?: number;
  index?: number;
  children?: ReactNode;
  onSelect?: () => void;
};

export function KpiCard({ label, value, caption, delta, index = 0, children, onSelect }: Props) {
  const reduce = useReducedMotion();

  return (
    <motion.article
      // `layout` is what makes filtering feel like the software understood the
      // click: cards move to their new positions instead of blinking.
      layout
      layoutId={`kpi-${label}`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: EASE, delay: reduce ? 0 : index * 0.05 }}
      whileHover={
        reduce
          ? undefined
          : { y: -4, transition: { type: "spring", stiffness: 400, damping: 28 } }
      }
      onClick={onSelect}
      className="group cursor-pointer rounded-2xl border border-black/5 bg-white p-6
                 shadow-[0_1px_2px_rgba(0,0,0,0.04)] transition-shadow
                 hover:shadow-[0_8px_30px_rgba(0,0,0,0.08)]
                 dark:border-white/8 dark:bg-neutral-900 dark:shadow-none"
    >
      <h3 className="text-sm font-medium text-neutral-500 dark:text-neutral-400">
        {label}
      </h3>

      {/* tabular-nums stops the layout jittering when a live value updates. */}
      <p className="mt-2 text-4xl font-semibold tabular-nums tracking-tight
                    text-neutral-900 dark:text-neutral-50">
        {value}
      </p>

      {delta !== undefined && (
        <span
          className={
            "mt-2 inline-flex items-center gap-1 text-sm font-medium " +
            (delta >= 0
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400")
          }
        >
          {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}%
        </span>
      )}

      {/* Plain-language caption. This does more work than any axis label. */}
      {caption && (
        <p className="mt-3 text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
          {caption}
        </p>
      )}

      {children}
    </motion.article>
  );
}
