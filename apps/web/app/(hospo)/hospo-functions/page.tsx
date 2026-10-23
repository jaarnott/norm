import type { Metadata } from 'next';
import HospoLanding, { Board, NormChat, type HospoProduct } from '../_components/HospoLanding';

export const metadata: Metadata = {
  title: { absolute: 'Hospo Functions — from the first enquiry to the final invoice' },
  description: 'Every function enquiry in one place, answered the same day, and moved through proposal, deposit and run sheet. Free for one venue.',
};

// Placeholder plans: the prices and limits are not set yet.
const PRODUCT: HospoProduct = {
  variant: 'functions',
  name: 'Hospo Functions',
  darkHero: true,
  nav: [['How it works', '#how'], ['Client portal', '#portal'], ['Pricing', '#pricing']],
  title: 'From the first enquiry to the final invoice.',
  lead: 'Every function enquiry lands in one place, gets a reply the same day, and moves through proposal, deposit and run sheet without a spreadsheet in sight. For venues that host birthdays, work dos and weddings.',
  boardLabel: 'The functions board: three new enquiries, two proposals sent and four confirmed events, with a farewell on Saturday waiting on a deposit.',
  board: (
    <Board
      title="Functions, Harbourside Kitchen" sub="This month · $18,400 confirmed"
      pill="1 deposit due" pillStyle={{ color: '#9a3d1f', background: '#ffe7de' }}
      cols={[
        { label: 'New enquiry · 3', cards: [{ name: 'Mia’s 30th', note: 'Sat 22 Nov · 40 guests' }, { name: 'Team dinner', note: 'Thu 4 Dec · 18 guests' }], more: '+1 more' },
        { label: 'Proposal sent · 2', cards: [{ name: 'Harris wedding', note: 'Opened twice, yesterday' }, { name: 'Xmas party', note: 'Sent Monday' }] },
        { label: 'Confirmed · 4', cards: [{ name: 'Sam’s farewell', note: 'Sat · deposit due today', hot: true }], more: '+3 more' },
      ]}
    />
  ),
  parts: {
    heading: 'One app for the whole booking.',
    lead: 'Nobody wants proposals without the enquiries behind them, so it’s all in one place, across every venue you run.',
    items: [
      { id: 'how', name: 'Enquiries and inboxes', body: 'Each venue’s functions inbox, sorted into enquiries, with nothing lost in a shared mailbox.' },
      { name: 'Proposals', body: 'Priced from your own packages and minimum spends, in your venue’s look, sent in minutes.' },
      { name: 'Events', body: 'Deposits, numbers, menus and the run sheet for the night, in one record per event.' },
      { id: 'portal', name: 'Client portal', body: 'Your client confirms numbers, picks the menu and pays the deposit themselves, from a link.' },
    ],
  },
  norm: {
    heading: 'Add Norm, and the enquiry is answered before you’ve had coffee.',
    body: 'Hospo Functions works on its own. Add Norm, an AI operations team for hospitality, and its Functions agent reads each enquiry, checks the space is free, drafts the reply and the proposal from your own packages, and chases deposits. It asks before anything goes to a client.',
    note: 'Add it any time from inside Hospo Functions, on any plan. Your enquiries and events stay exactly where they are.',
    chatLabel: 'A Norm conversation: the Functions agent has drafted a reply and a proposal for a 40-person birthday enquiry and asks before sending.',
    chat: (
      <NormChat
        agent="Functions"
        reply="A new enquiry came in overnight: Mia’s 30th, Saturday 22 November, about 40 guests, wants the deck. The deck is free that night. I’ve drafted a reply and a proposal on the cocktail package with a $2,500 minimum spend, like the last three Saturday birthdays."
        approval="Send reply and proposal to Mia" buttons={['Edit first', 'Send']}
      />
    ),
  },
  plans: [
    { name: 'Free', price: '$0', per: 'for one venue', blurb: 'For a venue taking the odd booking.', items: ['Up to 10 enquiries a month', 'One venue’s functions inbox', 'Events and run sheets'] },
    { name: 'Pro', price: '$49', per: 'a month per venue', blurb: 'For venues where functions are a business.', items: ['Unlimited enquiries', 'Proposals in your venue’s look', 'Client portal with deposits'], featured: true },
    { name: 'Group', price: 'Custom', per: 'for 5 or more venues', blurb: 'For groups selling functions across venues.', items: ['Everything in Pro', 'Every venue’s inbox in one place', 'Group reporting on functions revenue'] },
  ],
  addOn: { agent: 'Functions', points: ['Answers each enquiry the same day', 'Drafts the proposal from your own packages', 'Chases deposits and final numbers'] },
  other: { name: 'Hospo HR', href: '/hospo-hr' },
};

export default function HospoFunctionsPage() {
  return <HospoLanding p={PRODUCT} />;
}
