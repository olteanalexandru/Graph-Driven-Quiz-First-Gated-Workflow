import type { BusContext } from '../../context';
import { capXp } from '../../../helpers/xp';
import type { ProgressCommand } from './messages';

type AwardXp = Extract<ProgressCommand, { type: 'progress/awardXp' }>;

// One named handler per command. Business rules live in pure helpers (capXp),
// side effects go through ctx.services, results go out as events.
export async function handleAwardXp(cmd: AwardXp, ctx: BusContext): Promise<void> {
  const amount = capXp(cmd.amount);
  if (amount === 0) return;
  await ctx.services.progress.awardXp(amount);
  ctx.emit({ type: 'progress/xpAwarded', amount, reason: cmd.reason });
}
