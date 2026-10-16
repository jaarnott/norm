'use client';

/**
 * Settings → Venues: one venue's Functions profile — its time zone status,
 * areas, function settings and event fields.
 */
import PageState from '../../ui/PageState';
import { timeLabel } from '../shared/format';
import type { FnMeta, FnVenue } from '../shared/meta';
import AreasSection from './AreasSection';
import EnabledFieldsSection from './EnabledFieldsSection';
import VenueSettingsSection from './VenueSettingsSection';
import type { Area, VenueProfile } from './types';
import { NormSettingsButton, Notice } from './ui';

export default function VenuesTab({ meta, venue, profile, loading, error, setProfile, reload, onMetaChanged }: {
  meta: FnMeta;
  venue: FnVenue;
  profile: VenueProfile | null;
  loading: boolean;
  error: string | null;
  setProfile: (update: (p: VenueProfile) => VenueProfile) => void;
  reload: () => void;
  onMetaChanged: () => void;
}) {
  if (error) return <PageState kind="error" title={`Couldn’t load ${venue.name}`} detail={error} />;
  if (loading || !profile) return <PageState kind="loading" title={`Loading ${venue.name}…`} />;

  const canWrite = meta.can_write;
  const setAreas = (update: (areas: Area[]) => Area[]) => setProfile((p) => ({ ...p, areas: update(p.areas) }));
  const onSettingsSaved = (next: VenueProfile) => {
    setProfile(() => next);
    onMetaChanged();
  };
  const tz = profile.venue.timezone;

  return (
    <div style={{ maxWidth: 980 }}>
      {!venue.timezone_valid ? (
        <Notice tone="warn" style={{ marginBottom: 20 }} action={<NormSettingsButton tab="venues" label="Open venue settings" />}>
          <strong>{venue.name} has no valid time zone{tz ? ` (“${tz}” isn’t one Norm knows)` : ''}.</strong>{' '}
          Event dates and times are kept in the venue’s own time, so Functions can’t save bookings or options here until it’s
          set. Set it in Norm’s Settings → Venues.
        </Notice>
      ) : (
        <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          Times are in {tz?.replace(/_/g, ' ')}
          {profile.venue.day_start_time ? ` · the trading day starts at ${timeLabel(profile.venue.day_start_time)}` : ''}.
        </p>
      )}

      <AreasSection
        key={`areas-${venue.id}`}
        venueId={venue.id}
        areas={profile.areas}
        setAreas={setAreas}
        canWrite={canWrite}
        canGovern={profile.can_govern_approvals}
        bookingTypes={meta.booking_types}
        onReload={reload}
      />
      <VenueSettingsSection
        key={`settings-${venue.id}`}
        venueId={venue.id}
        settings={profile.settings}
        members={meta.members}
        canWrite={canWrite}
        canGovern={profile.can_govern_approvals}
        onSaved={onSettingsSaved}
      />
      <EnabledFieldsSection
        key={`fields-${venue.id}`}
        venueId={venue.id}
        settings={profile.settings}
        enabled={profile.enabled_fields}
        meta={meta}
        canWrite={canWrite}
        onSaved={onSettingsSaved}
      />
    </div>
  );
}
