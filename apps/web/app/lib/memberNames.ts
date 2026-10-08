/** How a team member is named in running text and labels — sentence case,
 *  never the raw slug ("time_attendance" → "Time & attendance"). */
const MEMBER_NAMES: Record<string, string> = {
  home: 'Norm',
  procurement: 'Procurement',
  hr: 'HR',
  time_attendance: 'Time & attendance',
  marketing: 'Marketing',
  reports: 'Reports',
  executive_chef: 'Executive chef',
  app_builder: 'App builder',
};

export function memberName(id: string): string {
  if (MEMBER_NAMES[id]) return MEMBER_NAMES[id];
  const words = id.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
