'use client';

import { createContext, useContext, useEffect, useMemo, useRef } from 'react';
import type { ReactNode } from 'react';
import type { BusContext, Services } from './context';
import { dispatch } from './dispatch';
import type { BusEvent, Command } from './messages';

type Listener = (event: BusEvent) => void;

export interface Bus {
  send(command: Command): Promise<void>;
  on(listener: Listener): () => void;
}

const BusCtx = createContext<Bus | null>(null);

// The one place where parts of the app talk to each other. Render it in the root
// layout (it is a Client Component; Server Components do not use the bus).
export function BusProvider({ services, children }: { services: Services; children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const bus = useMemo<Bus>(() => {
    const log: unknown[] = [];
    const ctx: BusContext = {
      services,
      log: (entry) => {
        log.push(entry);
      },
      emit: (event) => {
        log.push(event);
        listeners.current.forEach((listener) => listener(event));
      },
    };
    const api: Bus = {
      send: (command) => dispatch(command, ctx),
      on: (listener) => {
        listeners.current.add(listener);
        return () => {
          listeners.current.delete(listener);
        };
      },
    };
    // Test-only hook for Playwright. next.config must always define NEXT_PUBLIC_E2E
    // (env: { NEXT_PUBLIC_E2E: process.env.NEXT_PUBLIC_E2E ?? '0' }) so production
    // builds compile this branch away; security check NEXT-06 verifies that.
    if (process.env.NEXT_PUBLIC_E2E === '1' && typeof window !== 'undefined') {
      (window as unknown as { __appBus?: unknown }).__appBus = { send: api.send, log };
    }
    return api;
  }, [services]);
  return <BusCtx.Provider value={bus}>{children}</BusCtx.Provider>;
}

export function useBus(): Bus {
  const bus = useContext(BusCtx);
  if (!bus) throw new Error('useBus must be used inside <BusProvider>');
  return bus;
}

// Subscribe a component to one event type.
export function useBusEvent<T extends BusEvent['type']>(
  type: T,
  handler: (event: Extract<BusEvent, { type: T }>) => void,
): void {
  const bus = useBus();
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(
    () =>
      bus.on((event) => {
        if (event.type === type) latest.current(event as Extract<BusEvent, { type: T }>);
      }),
    [bus, type],
  );
}
