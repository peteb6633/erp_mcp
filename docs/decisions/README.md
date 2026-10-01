# Decision notes

The code says what this project does. These notes say why.

Every commit that changes code adds or updates a note here. The note records what was asked, what was decided and rejected, who decided, what risks were found, and which odd-looking bits of code are deliberate. Much of this code is written by AI models that make many choices on their own. Without these notes, those reasons exist nowhere once the session ends.

- **Start here:** read the newest notes first, and any note that names the file you are about to change.
- **Who decided:** a person's name means a person chose or approved it. "model" means a model chose and nobody has reviewed it. Question those first.
- **Writing one:** copy `TEMPLATE.md` to `YYYY-MM-DD-short-topic.md`.
- **No decision to record?** For a typo, formatting or version bump, put `Decision-Note: none - <reason>` in the commit message instead.
- **Notes go stale.** When a change overturns an old note, mark the old one "replaced by" the new one in the same commit.

The rule is enforced three ways: a Claude Code hook that stops the agent committing without a note, a git `commit-msg` hook for everyone else, and a CI check on every push.
