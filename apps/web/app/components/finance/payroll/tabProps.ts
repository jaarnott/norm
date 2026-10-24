/**
 * What the Payroll page hands every tab body: the meta (loaded once), the
 * entity and week chosen in the one header picker, and the one refresh
 * counter. A tab body never picks its own entity or week.
 */
import type { Entity, PayrollMeta } from './types';

export interface PayrollTabProps {
  meta: PayrollMeta;
  /** The chosen entity; null under All entities or when there are none. */
  entity: Entity | null;
  /** All entities chosen (Intercompany and Reconciliation only). */
  allVenues: boolean;
  /** The pay week's Monday. */
  weekStart: string;
  /** Bumped by the header's Refresh and after every write. */
  refresh: number;
  onChanged: () => void;
  /** Choose an entity (or 'all') in the header picker. */
  setVenue: (id: string) => void;
}
