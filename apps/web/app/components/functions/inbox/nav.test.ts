import { describe, expect, it } from 'vitest';
import { routeFromSearch, searchForRoute, searchWithoutInbox } from './nav';

describe('inbox deep links', () => {
  it('reads the routes staff links use', () => {
    expect(routeFromSearch('?page=functions-inbox')).toEqual({ view: 'list' });
    expect(routeFromSearch('?page=functions-inbox&mail_thread=acc-1:18f2a')).toEqual({
      view: 'thread',
      accountId: 'acc-1',
      threadId: '18f2a',
    });
    expect(routeFromSearch('?page=functions-inbox&view=proposal&enquiry=e1')).toEqual({ view: 'proposal', enquiryId: 'e1' });
    expect(routeFromSearch('?page=functions-inbox&view=proposal')).toEqual({ view: 'list' });
    expect(routeFromSearch('?view=compose&enquiry=e2')).toEqual({ view: 'compose', enquiryId: 'e2' });
    expect(routeFromSearch('?view=compose')).toEqual({ view: 'compose', enquiryId: null });
  });

  it('writes routes without disturbing other params', () => {
    const s = searchForRoute('?foo=1&view=proposal&enquiry=old', { view: 'thread', accountId: 'a', threadId: 't' });
    const sp = new URLSearchParams(s);
    expect(sp.get('foo')).toBe('1');
    expect(sp.get('page')).toBe('functions-inbox');
    expect(sp.get('mail_thread')).toBe('a:t');
    expect(sp.get('view')).toBeNull();
    expect(sp.get('enquiry')).toBeNull();
    expect(routeFromSearch(searchForRoute('', { view: 'proposal', enquiryId: 'e9' }))).toEqual({ view: 'proposal', enquiryId: 'e9' });
    expect(routeFromSearch(searchForRoute('?mail_thread=a:t', { view: 'list' }))).toEqual({ view: 'list' });
  });

  it('clears its params when the inbox closes, and only then', () => {
    expect(searchWithoutInbox('?page=functions-inbox&mail_thread=a:t&foo=1')).toBe('?foo=1');
    expect(searchWithoutInbox('?page=functions-inbox')).toBe('');
    expect(searchWithoutInbox('?page=functions-events&event=e1')).toBe('?page=functions-events&event=e1');
  });
});
