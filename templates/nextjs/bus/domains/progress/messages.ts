// Messages owned by the progress domain. The type prefix must match the folder name.
// After adding a message: write its handler in handlers.ts, then run node scripts/bus-gen.mjs.

export type ProgressCommand =
  | { type: 'progress/awardXp'; amount: number; reason: string };

export type ProgressEvent =
  | { type: 'progress/xpAwarded'; amount: number; reason: string };

// Typed builders. Components and Playwright specs import these instead of writing
// object literals, which also gives the code graph an edge from each caller.
export const awardXp = (amount: number, reason: string): ProgressCommand => ({
  type: 'progress/awardXp',
  amount,
  reason,
});
