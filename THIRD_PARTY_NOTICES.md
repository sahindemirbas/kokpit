# Third-party notices

Kokpit itself is released under the MIT licence (see `LICENSE`). This file
lists the third-party work Kokpit builds on and the terms that apply to it.

## Avenox Beyin

The second-brain conventions (vault layout, `Threads.md`, `Last-Session.md`,
receipts, the per-message context hook and the "update, never rewrite" rule)
come from Avenox Beyin, https://github.com/avenoxai/avenoxbeyin.

Status as of this commit: the `kokpit/` package contains no code copied from
Beyin. `kokpit/modules/brain/reader.py` was written for Kokpit and only parses
files that follow the Beyin conventions. `docs/brain-protocol.md` is Kokpit's
own description of the contract.

If a hook script or any other file derived from Beyin is added later, that file
must carry the notice below in its header together with a line describing the
modification, and this section must be updated to name the file.

```
MIT License

Copyright (c) 2026 Avenox

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Python standard library

The package depends only on the Python standard library (`http.server`,
`sqlite3`, `tomllib` and friends), Python Software Foundation License.

## Data sources (planned modules)

Modules that read external services (events, weather, calendars, social
accounts) are not part of this skeleton. Each one will list the source, its
terms and the data it reads in `docs/connections.md` before it is enabled.
