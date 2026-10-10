# Jokes Arena design

Reference: the user's pasted OpenAI design context, supplied 2026-10-10.
The current interface applies its white background, near-black text, system
fonts, muted purple accent `#8e8ea0`, 8px spacing units, 5px corners, and
768px breakpoint. No shadows or gradients.

The reference's white-on-purple contrast claim is inaccurate. Use dark text on
the purple navigation button. Use a dark outline for keyboard focus. Body copy
uses `#595961` on white. Joke cards are native buttons, accessible by keyboard.

Desktop cards sit side by side. Below 768px, cards stack vertically. The header
navigation wraps into its own row. The leaderboard table scrolls horizontally
inside its container on narrow screens without widening the page.

Battle has two choices: select the first joke or select the second joke.
No tie, neither-funny, or skip options. Cards contain one anonymous model line,
the joke, and a selection hint. Model identities appear in the leaderboard.

The leaderboard shows rank, model name, win rate, wins, losses, comparisons, and
unique jokes. Unvoted models display 'No votes' and no rank. Equal win rates
share a rank. Include pool size and an explicit small-sample explanation.

Use plain text for generated jokes. Preserve visible loading, save, failure,
and completed-pool states. Successful votes advance automatically. Failed votes
retain the pair. Failed next-pair fetches retry without submitting a saved vote.
