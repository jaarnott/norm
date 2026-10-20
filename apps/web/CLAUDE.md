# Norm web app

## Pages fill their width

A Settings section or a functional page fills the column it is given. Don't put
a `maxWidth` on a page's outer box: the Settings column (`.n-page`) already
gives the padding, and a capped page sits narrow and off to one side on a wide
screen.

Cap only a single control or a paragraph of prose (an input, help text under a
heading), where a very long line is hard to read, and a dialog.

Why this is written down: settings pages were built by copying an existing one,
and the copied `maxWidth: 860` (or 800, 900, 720) came along each time, until
the owner asked on 19 Oct 2026 why the pages kept coming out narrow.
