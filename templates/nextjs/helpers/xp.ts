// Pure helper: no React, no I/O, easy to unit test and property test.
export const MAX_XP_PER_AWARD = 50;

export function capXp(amount: number): number {
  if (!Number.isFinite(amount) || amount <= 0) return 0;
  return Math.min(Math.floor(amount), MAX_XP_PER_AWARD);
}
