# Privacy and publication boundaries

These rules bind this repository and every future release. They come from the author's own decision record and are not negotiable in pull requests.

## Never enters this repository

- CVs, notes, chat or session contents, calendar data, account metrics, databases (`*.db`, `*.sqlite*`), tokens and keys, device addresses (LAN, VPN, hostnames), personal screenshots.
- The author's own vault, configuration, `.env`, logs and cron lines.
- Git history, test fixtures and log samples are in scope of this rule: fixtures are synthetic and labelled as such.

Enforcement: `.gitignore` for the obvious patterns, a pre-commit secret scan before the first public release, and a manual review of every fixture.

## Never leaves the user's device without a stated reason

- Any module that sends data to an external model or service lists the fields it sends on the Connections screen, and the switch is off by default.
- CV matching needs skills, experience and targets. It does not need name, phone, e-mail or address, and these are never sent.
- Publishing to a social platform happens only after an explicit approval per item.
- A calendar ICS address is treated as a secret: it is never stored in the database, and API answers, the health log and error messages show it masked to scheme and host only.

## Demo content

If screenshots or sample data are needed, they are generated from clearly fictional data and labelled "example". No real account, person or message appears in them.

## Promises we do not make

- We do not promise to bypass platform restrictions (login walls, rate limits, API limitations). Where a platform does not offer an API for something, the docs say it is not possible, not that we work around it.
- We do not promise reliability for sources we scrape; each one is listed with its last successful update so the user sees when it breaks.

## Licence and third-party rights

Kokpit is MIT licensed (`LICENSE`). The review behind that choice: the package uses only the Python standard library, and the Beyin conventions it follows are MIT with no code copied (`THIRD_PARTY_NOTICES.md`). Data-source and scraping terms are reviewed per module when that module is added, not once for the whole project. The repository stays private for now. Going public needs the author's explicit decision; nobody else can flip that switch.
