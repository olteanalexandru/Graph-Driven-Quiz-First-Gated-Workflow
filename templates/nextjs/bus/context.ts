import type { BusEvent } from './messages';

// Side effects the handlers may use. The app passes real implementations to
// <BusProvider services={...}>; unit tests pass fakes. Add one entry per service.
export interface Services {
  progress: {
    // Calls a server action; the server re-checks the user and validates the amount.
    awardXp(amount: number): Promise<void>;
  };
}

export interface BusContext {
  emit(event: BusEvent): void;
  log(entry: unknown): void;
  services: Services;
}
