import type { Metadata } from 'next';
import HospoLanding, { Board, NormChat, type HospoProduct } from '../_components/HospoLanding';

export const metadata: Metadata = {
  title: { absolute: 'Hospo HR — hiring, training and reviews for hospitality teams' },
  description: 'Post the job, run the trial shift, get the new starter trained and signed off on the floor, then keep the reviews coming. Free for one venue.',
};

// Placeholder plans: the prices and limits are not set yet.
const PRODUCT: HospoProduct = {
  variant: 'hr',
  name: 'Hospo HR',
  nav: [['Hiring', '#hiring'], ['Training', '#training'], ['Reviews', '#reviews'], ['Pricing', '#pricing']],
  title: 'Hiring, training and reviews for hospitality teams.',
  lead: 'Post the job, run the trial shift, get the new starter trained and signed off on the floor, then keep the reviews coming. One record per person, from the first application to their first anniversary.',
  boardLabel: 'The hiring board for a bar manager role: four applied, two on a trial shift, one offer.',
  board: (
    <Board
      title="Bar manager, Harbourside Kitchen" sub="Posted 9 days ago · 7 candidates"
      pill="Open" pillStyle={{ color: '#0f7f75', background: '#e3f6f3' }}
      cols={[
        { label: 'Applied · 4', cards: [{ name: 'Sam K.', note: '3 yrs cocktail bars' }, { name: 'Priya S.', note: 'Duty manager, 2 yrs' }], more: '+2 more' },
        { label: 'Trial shift · 2', cards: [{ name: 'Tom E.', note: 'Fri 6pm, with Mia' }, { name: 'Josh K.', note: 'Sat 5pm, with Mia' }] },
        { label: 'Offer · 1', cards: [{ name: 'Aroha M.', note: 'Starts 3 Nov · onboarding ready', hot: true }] },
      ]}
    />
  ),
  parts: {
    heading: 'Three apps. One record per person.',
    lead: 'Use the ones you need: run Hiring today and add Training when the first new starter arrives. Built for how venues actually hire and keep people: fast, on the floor, between services.',
    items: [
      { id: 'hiring', name: 'Hiring', body: 'Openings, a candidate pipeline and a talent pool for the people you’d hire again. Offers and onboarding from the same card.', tags: 'Job posts · Pipeline · Trial shifts · Talent pool' },
      { id: 'training', name: 'Training', body: 'Programs for each role, a plan for each new starter, and sign-offs done on shift by whoever is training them.', tags: 'Programs · Plans · On-shift sign-offs · Tracker' },
      { id: 'reviews', name: 'Reviews', body: 'Review cycles that actually run: a capability framework for each role, reminders when someone is due, and a record that follows the person between venues.', tags: 'Cycles · Capability frameworks · Reminders' },
    ],
  },
  norm: {
    heading: 'Add Norm, and an AI agent does the legwork.',
    body: 'Hospo HR works on its own. Add Norm, an AI operations team for hospitality, and its HR agent works inside Hospo HR: it reads the CVs and shortlists, drafts the ad, builds each new starter’s training plan from the role, and reminds managers when a review is due. You approve. It does.',
    note: 'Add it any time from inside Hospo HR, on any plan. Your people, roles and records stay exactly where they are.',
    chatLabel: 'A Norm conversation: a manager asks for a shortlist for the bar manager role, Norm’s HR agent names three candidates and offers to book trial shifts.',
    chat: (
      <NormChat
        agent="HR" ask="Shortlist the bar manager applicants for me."
        reply="Three stand out: Sam K. (three years in cocktail bars, available weekends), Priya S. (duty manager, has run a till of this size) and Tom E. (already on a trial shift Friday). Want me to book Sam and Priya for trial shifts this weekend? Mia is rostered both nights."
        approval="Book 2 trial shifts" buttons={['Not now', 'Book them']}
      />
    ),
  },
  plans: [
    { name: 'Free', price: '$0', per: 'for one venue', blurb: 'For one venue getting organised.', items: ['Hiring and Training', 'One venue, up to 15 people', 'Job posts and a candidate board'] },
    { name: 'Pro', price: '$39', per: 'a month per venue', blurb: 'For venues hiring and training every month.', items: ['Hiring, Training and Reviews', 'Unlimited people and managers', 'Trial shifts, sign-offs on shift and review cycles'], featured: true },
    { name: 'Group', price: 'Custom', per: 'for 5 or more venues', blurb: 'For groups with a head office.', items: ['Everything in Pro', 'Head office view across venues', 'People move between venues with their record'] },
  ],
  addOn: { agent: 'HR', points: ['Reads the CVs and shortlists', 'Builds each new starter’s training plan', 'Reminds managers when a review is due'] },
  other: { name: 'Hospo Functions', href: '/hospo-functions' },
};

export default function HospoHrPage() {
  return <HospoLanding p={PRODUCT} />;
}
