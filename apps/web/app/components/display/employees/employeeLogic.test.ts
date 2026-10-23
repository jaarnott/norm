import { describe, expect, it } from 'vitest';
import {
  applyFilters, EMPTY_FILTERS, extractDirectory, extractPerson, formatDay, goesBy, managerName,
  matchLocation, NO_TEAM, readError, sentenceCase, sortRows, teamCounts, tenure, venueKey,
  type DirectoryRow,
} from './employeeLogic';

// The shapes get_hr returned on 23 Oct 2026 (La Zeppa's connection).
const directoryBody = {
  success: true,
  error: null,
  data: {
    view: 'employees',
    current_staff: 3,
    by_venue: { 'Auckland Office': 1, 'La Zeppa': 2 },
    employees: [
      { id: '1456', name: 'Ashley Bartlett', preferred: 'Ash', job_title: 'Group Bookings and Functions Executive', venue: 'Auckland Office', division: 'Management', company: 'Cook Brothers International Ltd', work_email: null, mobile: '0211513187' },
      { id: 1522, name: 'Aliana Henderson', preferred: null, job_title: 'Floor Team', venue: 'La Zeppa', division: 'Front of House', company: 'Cook Brothers Bars Ponsonby Ltd', work_email: null, mobile: '02102831911' },
      { id: '1600', name: 'Zed Cook', preferred: 'Zed', job_title: 'Chef de Partie', venue: 'La Zeppa', division: null, company: null, work_email: 'zed@example.com', mobile: null, date_of_birth: '1990-01-01', pay_rate: 31.5 },
    ],
    venue_id: '13dac930',
  },
};

const personBody = {
  success: true,
  error: null,
  data: {
    view: 'employees', employee_id: '1469', displayName: 'Felipe Araya', firstName: 'Felipe', lastName: 'Araya',
    jobTitle: 'Group Executive Chef', department: 'Cook Brothers International Ltd', location: 'Auckland Office',
    division: 'Management', status: 'Active', employmentHistoryStatus: 'Full-Time', hireDate: '2025-06-04',
    workEmail: 'felipe@cbhg.co.nz', mobilePhone: '+64225370137', supervisor: 'Arnott, James',
    // never shown, even if the tool ever returns them
    dateOfBirth: '1980-02-03', sin: '123-456-789', payRate: '30.00 NZD', address1: '1 Home St', bankAccount: '12-3456',
  },
};

describe('extractDirectory', () => {
  const doc = extractDirectory(directoryBody)!;

  it('reads the wrapped document and stringifies ids', () => {
    expect(doc.current_staff).toBe(3);
    expect(doc.by_venue['La Zeppa']).toBe(2);
    expect(doc.employees.map((e) => e.id)).toEqual(['1456', '1522', '1600']);
  });

  it('keeps only the allowed fields — a sensitive one never reaches the screen', () => {
    const zed = doc.employees[2] as unknown as Record<string, unknown>;
    expect(zed.date_of_birth).toBeUndefined();
    expect(zed.pay_rate).toBeUndefined();
    expect(Object.keys(zed).sort()).toEqual(['company', 'division', 'id', 'job_title', 'mobile', 'name', 'preferred', 'venue', 'work_email']);
  });

  it('leaves status and start date undefined when the directory does not carry them', () => {
    expect(doc.employees[0].hire_date).toBeUndefined();
    expect(doc.employees[0].status).toBeUndefined();
    const withDates = extractDirectory({ employees: [{ id: 1, name: 'A', hire_date: '2024-01-02', status: 'Inactive' }] })!;
    expect(withDates.employees[0].hire_date).toBe('2024-01-02');
    expect(withDates.employees[0].status).toBe('Inactive');
  });

  it('is null for anything that is not a directory', () => {
    expect(extractDirectory({})).toBeNull();
    expect(extractDirectory(personBody)).toBeNull();
    expect(extractDirectory(null)).toBeNull();
  });
});

describe('extractPerson', () => {
  it('reads the record and drops everything outside the allow-list', () => {
    const p = extractPerson(personBody)! as unknown as Record<string, unknown>;
    expect(p.name).toBe('Felipe Araya');
    expect(p.hireDate).toBe('2025-06-04');
    expect(p.supervisor).toBe('Arnott, James');
    for (const k of ['dateOfBirth', 'sin', 'payRate', 'address1', 'bankAccount']) expect(p[k]).toBeUndefined();
  });

  it("treats BambooHR's empty date as no date", () => {
    expect(extractPerson({ employee_id: '5', terminationDate: '0000-00-00' })!.terminationDate).toBeNull();
  });
});

describe('venues', () => {
  it('matches Norm venue names to BambooHR locations the way get_hr does', () => {
    const locations = ['Glass Goose', 'Mr Murdochs', 'Freeman & Grey', 'La Zeppa'];
    expect(matchLocation('The Glass Goose', locations)).toBe('Glass Goose');
    expect(matchLocation("Mr Murdoch's", locations)).toBe('Mr Murdochs');
    expect(matchLocation('Freeman and Grey', locations)).toBe('Freeman & Grey');
    expect(matchLocation('Bessie & Royals', locations)).toBeNull();
    expect(matchLocation('La', locations)).toBeNull(); // a whole name, never part of one
    expect(venueKey('  The  Glass-Goose ')).toBe('glass goose');
  });
});

describe('filters', () => {
  const rows = extractDirectory(directoryBody)!.employees;

  it('searches every word across name, preferred name, role, team and venue', () => {
    expect(applyFilters(rows, { ...EMPTY_FILTERS, query: 'ash' }).map((r) => r.id)).toEqual(['1456']);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, query: 'chef zeppa' }).map((r) => r.id)).toEqual(['1600']);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, query: 'front of house' }).map((r) => r.id)).toEqual(['1522']);
  });

  it('filters by venue, team (including none set) and role', () => {
    expect(applyFilters(rows, { ...EMPTY_FILTERS, location: 'La Zeppa' })).toHaveLength(2);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, team: NO_TEAM }).map((r) => r.id)).toEqual(['1600']);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, team: 'Management' }).map((r) => r.id)).toEqual(['1456']);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, role: 'Floor Team' }).map((r) => r.id)).toEqual(['1522']);
  });

  it('counts the directory as current staff unless a status says otherwise', () => {
    expect(applyFilters(rows, { ...EMPTY_FILTERS, status: 'active' })).toHaveLength(3);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, status: 'inactive' })).toHaveLength(0);
    const statusOf = (r: DirectoryRow) => (r.id === '1600' ? 'Inactive' : 'Active');
    expect(applyFilters(rows, { ...EMPTY_FILTERS, status: 'inactive' }, statusOf).map((r) => r.id)).toEqual(['1600']);
    expect(applyFilters(rows, { ...EMPTY_FILTERS, status: 'all' }, statusOf)).toHaveLength(3);
  });

  it('can leave one filter out (the team tiles count across teams)', () => {
    const f = { ...EMPTY_FILTERS, team: 'Management', location: 'La Zeppa' };
    expect(applyFilters(rows, f)).toHaveLength(0);
    expect(applyFilters(rows, f, undefined, { team: true })).toHaveLength(2);
  });

  it('sorts by venue then name and counts teams biggest first', () => {
    expect(sortRows([...rows].reverse()).map((r) => r.id)).toEqual(['1456', '1522', '1600']);
    expect(teamCounts(rows)).toEqual([{ team: 'Front of House', count: 1 }, { team: 'Management', count: 1 }]);
  });
});

describe('labels', () => {
  it('turns BambooHR spellings into plain words', () => {
    expect(managerName('Arnott, James')).toBe('James Arnott');
    expect(managerName('Leah Aird')).toBe('Leah Aird');
    expect(sentenceCase('Full-Time')).toBe('Full-time');
    expect(goesBy('Ashley Bartlett', 'Ash')).toBe('Ash');
    expect(goesBy('Zed Cook', 'zed')).toBeNull();
    expect(formatDay('2025-06-04')).toBe('4 Jun 2025');
    expect(formatDay(null)).toBe('—');
  });

  it('says how long someone has been there', () => {
    const today = new Date(2026, 9, 23); // 23 Oct 2026
    expect(tenure('2025-06-04', today)).toBe('1 year 4 months');
    expect(tenure('2024-10-23', today)).toBe('2 years');
    expect(tenure('2026-10-01', today)).toBe('under a month');
    expect(tenure('2026-09-24', today)).toBe('under a month');
    expect(tenure('2026-09-23', today)).toBe('1 month');
    expect(tenure('2026-11-01', today)).toBe('starts soon');
    expect(tenure(null, today)).toBeNull();
  });

  it("keeps what happened from get_hr's errors and drops the note to Norm", () => {
    expect(readError("BambooHR refused Norm's API key reading the staff directory (401). An admin needs to re-enter the BambooHR key in Norm's connection settings. Tell the user that — don't answer from another system instead.").kind).toBe('refused');
    expect(readError('No credentials configured for bamboohr').kind).toBe('not_connected');
    expect(readError("Couldn't read the staff directory from BambooHR (unexpected response (html)) — this is a BambooHR failure, not missing data; try again.")).toEqual({
      kind: 'failed', text: "Couldn't read the staff directory from BambooHR.",
    });
    // What the overloaded dev box produced on 23 Oct: no SSL jargon on the page.
    expect(readError("Couldn't read the staff directory from BambooHR (Network error: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] sslv3 alert handshake failure (_ssl.c:1000)) — this is a BambooHR failure, not missing data; try again.")).toEqual({
      kind: 'failed', text: 'BambooHR couldn’t be reached just now. Try again in a minute.',
    });
  });
});
