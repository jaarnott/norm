'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Delete } from 'lucide-react';
import AppShell from '../app/page';
import LoginForm from '../components/auth/LoginForm';
import Icon from '../components/ui/Icon';
import { apiFetch, clearToken, getToken, setActiveOrg, setStoredUser, setToken } from '../lib/api';

/** Where the tablet keeps its own sign-in. The device token lives here only;
 *  norm_token holds whoever is acting right now (a person after their PIN,
 *  or the device itself between people). */
const DEVICE_KEY = 'norm_device';
const PIN_LENGTH = 5;
/** Two minutes without a tap and the tablet goes back to the PIN pad. */
const IDLE_MS = 2 * 60 * 1000;
/** Lock a little before the person's token runs out on the server, so the
 *  shell never sees a 401 (which would bounce the tablet to the login page). */
const EXPIRY_MARGIN_MS = 20 * 1000;

interface StoredDevice {
  token: string;
  id: string;
  name: string;
  venue_name?: string | null;
  pin_required: boolean;
}

interface Actor {
  name: string;
  /** When the person's token runs out (ms since the epoch). */
  until: number;
}

function readDevice(): StoredDevice | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    return d && typeof d.token === 'string' ? (d as StoredDevice) : null;
  } catch {
    return null;
  }
}

function writeDevice(device: StoredDevice): void {
  try { localStorage.setItem(DEVICE_KEY, JSON.stringify(device)); } catch { /* storage blocked: the page falls back to setup */ }
}

function clearDevice(): void {
  try { localStorage.removeItem(DEVICE_KEY); } catch { /* nothing to clear */ }
}

/** A call made as the device, whatever norm_token holds. */
function deviceFetch(token: string, path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  return fetch(path, { ...init, headers });
}

/** What the shell stores for the device between people. */
function actAsDevice(device: StoredDevice): void {
  setActiveOrg(null);
  setStoredUser({ id: device.id, email: '', full_name: device.name, role: 'user' });
  setToken(device.token);
}

/**
 * The kitchen tablet (docs/plans/staff-access.md, sections 5 and 11.7). A
 * manager signs it in once as a venue device; from then on it opens on a PIN
 * pad, and a PIN makes the shell act as that person until two minutes pass
 * without a tap, or they hand over. A tablet set up without a PIN runs as
 * the device itself.
 */
export default function TabletPage() {
  const [device, setDevice] = useState<StoredDevice | null | undefined>(undefined);
  const [phase, setPhase] = useState<'loading' | 'setup' | 'revoked' | 'locked' | 'acting'>('loading');
  const [actor, setActor] = useState<Actor | null>(null);

  // Which way in: no device stored → set-up; a device → ask the server
  // whether it still stands and whether it wants a PIN.
  useEffect(() => {
    const stored = readDevice();
    setDevice(stored);
    if (!stored) { setPhase('setup'); return; }
    let live = true;
    deviceFetch(stored.token, '/api/tablet/context')
      .then(async (res) => {
        if (!live) return;
        if (res.status === 401 || res.status === 403) {
          clearDevice();
          setPhase('revoked');
          return;
        }
        let pinRequired = stored.pin_required;
        if (res.ok) {
          const data = await res.json();
          const d = data?.device;
          if (d) {
            pinRequired = !!d.pin_required;
            const next = { ...stored, name: d.name ?? stored.name, venue_name: d.venue?.name ?? stored.venue_name, pin_required: pinRequired };
            writeDevice(next);
            setDevice(next);
          }
        }
        if (pinRequired) {
          setPhase('locked');
        } else {
          actAsDevice(stored);
          setPhase('acting');
        }
      })
      .catch(() => {
        // Offline for a moment: carry on the way it was last set up.
        if (!live) return;
        if (stored.pin_required) setPhase('locked');
        else { actAsDevice(stored); setPhase('acting'); }
      });
    return () => { live = false; };
  }, []);

  // Back to the pad: the device's own token goes back into the shell's slot.
  const lock = useCallback(() => {
    const d = readDevice();
    if (d) actAsDevice(d);
    setActor(null);
    setPhase('locked');
  }, []);

  // Two minutes without a tap, or the person's token about to run out.
  useEffect(() => {
    if (phase !== 'acting' || !actor) return;
    let idle = window.setTimeout(lock, IDLE_MS);
    const bump = () => { window.clearTimeout(idle); idle = window.setTimeout(lock, IDLE_MS); };
    const events: (keyof WindowEventMap)[] = ['pointerdown', 'keydown', 'touchstart', 'wheel'];
    for (const ev of events) window.addEventListener(ev, bump, { capture: true, passive: true });
    const expiry = window.setTimeout(lock, Math.max(1000, actor.until - EXPIRY_MARGIN_MS - Date.now()));
    return () => {
      window.clearTimeout(idle);
      window.clearTimeout(expiry);
      for (const ev of events) window.removeEventListener(ev, bump, { capture: true });
    };
  }, [phase, actor, lock]);

  const onPin = useCallback((name: string, token: string, expiresIn: number, userId: string) => {
    setActiveOrg(null);
    setStoredUser({ id: userId, email: '', full_name: name, role: 'user' });
    setToken(token);
    setActor({ name, until: Date.now() + expiresIn * 1000 });
    setPhase('acting');
  }, []);

  if (phase === 'loading' || device === undefined) return null;

  if (phase === 'setup') return <SetupFlow />;

  if (phase === 'revoked') {
    return (
      <Frame title="Norm">
        <div className="n-card" style={{ maxWidth: 420, margin: '48px auto', padding: 24, textAlign: 'center' }}>
          <h1 style={{ margin: '0 0 8px', fontSize: 'var(--fs-xl)', fontWeight: 700 }}>This tablet has been signed out</h1>
          <p style={{ margin: '0 0 20px', color: 'var(--text-soft)' }}>A manager signed it out from Norm. A manager can set it up again here.</p>
          <button type="button" className="n-pill-btn" onClick={() => setPhase('setup')}>Set up this tablet</button>
        </div>
      </Frame>
    );
  }

  if (phase === 'locked' && device) {
    return (
      <Frame title={device.name} subtitle={device.venue_name}>
        <PinPad device={device} onPin={onPin} onRevoked={() => { clearDevice(); setPhase('revoked'); }} />
      </Frame>
    );
  }

  // Acting: the ordinary shell, as the person who tapped their PIN (or as the
  // device when no PIN is asked for), under a slim header with Hand over.
  return (
    <div style={{ height: '100dvh', display: 'flex', flexDirection: 'column', overflow: 'hidden', backgroundColor: 'var(--canvas)' }}>
      <header data-testid="tablet-header" style={{
        flex: '0 0 auto', height: 40, padding: '0 12px', display: 'flex', alignItems: 'center', gap: 12,
        borderBottom: '1px solid var(--line)', backgroundColor: 'var(--canvas)', fontSize: 'var(--fs-sm)',
      }}>
        <span style={{ color: 'var(--text-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {device?.name}{device?.venue_name ? ` · ${device.venue_name}` : ''}
        </span>
        <span style={{ flex: 1 }} />
        {actor && (
          <button type="button" className="n-btn n-btn--secondary n-btn--sm" data-testid="tablet-handover" onClick={lock}>
            {actor.name} · Hand over
          </button>
        )}
      </header>
      <div className="n-tablet-shell" style={{ flex: 1, minHeight: 0 }}>
        <AppShell key={actor ? `${actor.name}:${actor.until}` : 'device'} />
      </div>
    </div>
  );
}

/** The page frame for the pad and the notices: a top bar, the canvas below. */
function Frame({ title, subtitle, children }: { title: string; subtitle?: string | null; children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--canvas)', lineHeight: 1.45 }}>
      <header style={{
        flex: '0 0 auto', boxSizing: 'content-box', height: 52, padding: 'env(safe-area-inset-top, 0px) 16px 0',
        display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--line)',
      }}>
        <span aria-hidden style={{ fontSize: 'var(--fs-xl)', fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--brand)' }}>Norm</span>
        <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{title}</span>
        {subtitle && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{subtitle}</span>}
      </header>
      <main style={{ flex: 1, padding: '0 16px' }}>{children}</main>
    </div>
  );
}

/** Five big digit boxes and a keypad. Wrong PINs are counted on the server;
 *  the sixth in five minutes locks the tablet for the rest of them. */
function PinPad({ device, onPin, onRevoked }: {
  device: StoredDevice;
  onPin: (name: string, token: string, expiresIn: number, userId: string) => void;
  onRevoked: () => void;
}) {
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [, setTick] = useState(0);
  const submitting = useRef(false);

  // Count the lock down so the keys come back on their own.
  useEffect(() => {
    if (!lockedUntil) return;
    const id = window.setInterval(() => {
      if (Date.now() >= lockedUntil) { setLockedUntil(null); setMessage(''); }
      else setTick(t => t + 1);
    }, 1000);
    return () => window.clearInterval(id);
  }, [lockedUntil]);

  const locked = !!lockedUntil && Date.now() < lockedUntil;

  const submit = useCallback(async (value: string) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    try {
      const res = await deviceFetch(device.token, '/api/tablet/pin', { method: 'POST', body: JSON.stringify({ pin: value }) });
      if (res.ok) {
        const data = await res.json();
        setPin('');
        setMessage('');
        onPin(data.user?.full_name || 'Staff', data.access_token, Number(data.expires_in) || 600, data.user?.id || '');
        return;
      }
      setPin('');
      if (res.status === 429) {
        const retry = Number(res.headers.get('Retry-After')) || 300;
        setLockedUntil(Date.now() + retry * 1000);
        setMessage('Locked for a few minutes. Too many wrong PINs were tried.');
      } else if (res.status === 401 || res.status === 403) {
        onRevoked();
      } else {
        setMessage("That PIN isn't right. Try again.");
      }
    } catch {
      setPin('');
      setMessage("We couldn't reach Norm. Check the connection and try again.");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }, [device.token, onPin, onRevoked]);

  const press = (digit: string) => {
    if (busy || locked) return;
    setMessage('');
    const next = (pin + digit).slice(0, PIN_LENGTH);
    setPin(next);
    if (next.length === PIN_LENGTH) void submit(next);
  };
  const back = () => { if (!busy) setPin(p => p.slice(0, -1)); };

  // A hardware keyboard works too.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (/^\d$/.test(e.key)) press(e.key);
      else if (e.key === 'Backspace') back();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pin, busy, locked]);

  const secondsLeft = lockedUntil ? Math.max(0, Math.ceil((lockedUntil - Date.now()) / 1000)) : 0;

  return (
    <div data-testid="tablet-pinpad" style={{ maxWidth: 420, margin: '0 auto', padding: '40px 0 32px', textAlign: 'center' }}>
      <h1 style={{ margin: '0 0 6px', fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>Tap your PIN</h1>
      <p style={{ margin: '0 0 24px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
        Everything you tick is recorded as yours. The tablet goes back to this screen after two minutes without a tap.
      </p>
      <div className="n-pin-boxes" aria-label={`${pin.length} of ${PIN_LENGTH} digits entered`} role="status">
        {Array.from({ length: PIN_LENGTH }, (_, i) => (
          <div key={i} className="n-pin-box" data-filled={i < pin.length}>{i < pin.length ? '•' : ''}</div>
        ))}
      </div>
      <p role="alert" data-testid="tablet-pin-message" style={{ minHeight: 22, margin: '14px 0 18px', fontSize: 'var(--fs-sm)', color: locked ? 'var(--warn)' : 'var(--error)' }}>
        {message}{locked && secondsLeft ? ` Try again in ${secondsLeft} seconds.` : ''}
      </p>
      <div className="n-keypad">
        {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map(d => (
          <button key={d} type="button" className="n-key" data-testid={`tablet-key-${d}`} disabled={busy || locked} onClick={() => press(d)}>{d}</button>
        ))}
        <button type="button" className="n-key n-key--quiet" disabled={busy || !pin} onClick={() => setPin('')}>Clear</button>
        <button type="button" className="n-key" data-testid="tablet-key-0" disabled={busy || locked} onClick={() => press('0')}>0</button>
        <button type="button" className="n-key n-key--quiet" aria-label="Delete the last digit" disabled={busy || !pin} onClick={back}>
          <Icon icon={Delete} size={22} tone="muted" />
        </button>
      </div>
    </div>
  );
}

interface VenueRow { id: string; name: string }
interface RoleRow { id: string; name: string; display_name: string; is_system?: boolean; permissions?: string[] }

/** The roles a tablet may act under, as the API allows them: never Owner, and
 *  nothing holding more than the person setting the tablet up holds here (a
 *  platform admin is held only to the Owner rule). */
function tabletRoles(roles: RoleRow[], me: { role?: string; permissions?: string[] } | null): RoleRow[] {
  const mine = new Set(me?.permissions ?? []);
  const admin = me?.role === 'admin';
  return roles.filter(r => r.name !== 'owner' && (admin || (r.permissions ?? []).every(p => mine.has(p))));
}

/** A manager signs the tablet in: their own login first, then which venue
 *  this is, what to call the tablet, the role it acts under and whether it
 *  asks for a PIN. The device token replaces the manager's sign-in here. */
function SetupFlow() {
  const [signedIn, setSignedIn] = useState<boolean>(() => !!getToken());
  const [orgId, setOrgId] = useState<string | null>(null);
  const [venues, setVenues] = useState<VenueRow[]>([]);
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [ready, setReady] = useState(false);
  const [venueId, setVenueId] = useState('');
  const [name, setName] = useState('');
  const [roleId, setRoleId] = useState('');
  const [pinRequired, setPinRequired] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!signedIn) return;
    let live = true;
    (async () => {
      try {
        const me = await apiFetch('/api/auth/me');
        if (!me.ok) { clearToken(); if (live) setSignedIn(false); return; }
        const user = await me.json();
        const org = user.active_organization_id as string | null;
        if (!live) return;
        setOrgId(org);
        if (!org) { setReady(true); return; }
        const [vRes, rRes] = await Promise.all([apiFetch('/api/venues'), apiFetch(`/api/organizations/${org}/roles`)]);
        const vs: VenueRow[] = vRes.ok ? ((await vRes.json()).venues ?? []) : [];
        const rs: RoleRow[] = tabletRoles(rRes.ok ? ((await rRes.json()).roles ?? []) : [], user);
        if (!live) return;
        setVenues(vs);
        setRoles(rs);
        if (vs.length) {
          setVenueId(vs[0].id);
          setName(`${vs[0].name} kitchen`);
        }
        const kitchen = rs.find(r => r.name === 'kitchen') ?? rs.find(r => r.name === 'team_member') ?? rs[0];
        if (kitchen) setRoleId(kitchen.id);
      } catch {
        if (live) setError("We couldn't load your venues. Check the connection and try again.");
      } finally {
        if (live) setReady(true);
      }
    })();
    return () => { live = false; };
  }, [signedIn]);

  if (!signedIn) {
    return (
      <div>
        <div style={{ padding: '16px 16px 0', textAlign: 'center', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
          <strong style={{ color: 'var(--text)', fontWeight: 600 }}>Set up this tablet.</strong> A manager signs in first; the tablet then gets a sign-in of its own.
        </div>
        <LoginForm onSuccess={(token, user) => { setStoredUser(user); setToken(token); setSignedIn(true); }} />
      </div>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!orgId || busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/devices`, {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, name: name.trim(), role_id: roleId, pin_required: pinRequired }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(res.status === 403
          ? 'Only a manager can set up a tablet. Ask one to sign in here.'
          : (typeof data.detail === 'string' ? data.detail : "The tablet couldn't be set up. Try again."));
        return;
      }
      const data = await res.json();
      const venue = venues.find(v => v.id === venueId);
      writeDevice({ token: data.token, id: data.device?.id ?? '', name: data.device?.name ?? name.trim(), venue_name: venue?.name ?? null, pin_required: pinRequired });
      // The manager's own sign-in leaves this tablet with them.
      clearToken();
      window.location.reload();
    } catch {
      setError("We couldn't reach Norm. Check the connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  const signOut = () => { clearToken(); setSignedIn(false); setReady(false); };

  return (
    <Frame title="Set up this tablet">
      <form onSubmit={submit} className="n-card" style={{ maxWidth: 440, margin: '32px auto', padding: 24 }}>
        <h1 style={{ margin: '0 0 6px', fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--text)' }}>Set up this tablet</h1>
        <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
          The tablet stays signed in as the venue until a manager signs it out from Settings → People.
        </p>
        {!ready ? (
          <p style={{ color: 'var(--muted)' }}>Loading your venues…</p>
        ) : !orgId ? (
          <p role="alert" style={{ color: 'var(--error)' }}>Your account isn&apos;t in an organisation, so it can&apos;t set up a tablet.</p>
        ) : venues.length === 0 ? (
          <p role="alert" style={{ color: 'var(--error)' }}>Your organisation has no venues yet. Add one in Settings → Venues first.</p>
        ) : (
          <>
            <label htmlFor="tablet-venue" className="n-label">Venue</label>
            <select id="tablet-venue" className="n-select" data-testid="tablet-setup-venue" value={venueId} onChange={e => { setVenueId(e.target.value); const v = venues.find(x => x.id === e.target.value); if (v && (!name || /kitchen$/.test(name))) setName(`${v.name} kitchen`); }} style={{ width: '100%', marginBottom: 14 }}>
              {venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
            <label htmlFor="tablet-name" className="n-label">What to call this tablet</label>
            <input id="tablet-name" className="n-input" data-testid="tablet-setup-name" value={name} onChange={e => setName(e.target.value)} required placeholder="La Zeppa kitchen" style={{ width: '100%', marginBottom: 14 }} />
            <label htmlFor="tablet-role" className="n-label">Acts under the role</label>
            <select id="tablet-role" className="n-select" data-testid="tablet-setup-role" value={roleId} onChange={e => setRoleId(e.target.value)} style={{ width: '100%', marginBottom: 14 }}>
              {roles.map(r => <option key={r.id} value={r.id}>{r.display_name || r.name}</option>)}
            </select>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 6, fontSize: 'var(--fs-base)', color: 'var(--text)', cursor: 'pointer' }}>
              <input type="checkbox" data-testid="tablet-setup-pin" checked={pinRequired} onChange={e => setPinRequired(e.target.checked)} style={{ marginTop: 3 }} />
              <span>
                Ask for a PIN
                <span style={{ display: 'block', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  Each person taps their PIN before they use the tablet, so what they tick is recorded as theirs. Without one, everything is recorded as the tablet.
                </span>
              </span>
            </label>
            {error && <p role="alert" style={{ margin: '12px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
            <button type="submit" className="n-pill-btn" data-testid="tablet-setup-submit" disabled={busy || !venueId || !roleId || !name.trim()} style={{ marginTop: 20 }}>
              {busy ? 'Setting up…' : 'Set up this tablet'}
            </button>
          </>
        )}
        {error && ready && (!orgId || venues.length === 0) && <p role="alert" style={{ margin: '12px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
        <div style={{ marginTop: 16, textAlign: 'center', fontSize: 'var(--fs-sm)' }}>
          <button type="button" className="n-btn n-btn--link" onClick={signOut}>Not you? Sign out</button>
        </div>
      </form>
    </Frame>
  );
}
