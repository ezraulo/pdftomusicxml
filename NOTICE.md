# Notices

This project is licensed under the **GNU Affero General Public License v3.0** (see
[LICENSE](./LICENSE)). It runs as a network service, so AGPL §13 applies: if you let
other people use a modified version over a network, offer them its source. The upload
page links to this repository for that reason.

## Bundled or adapted third-party work

### Audiveris (AGPL-3.0)

The Docker image builds and bundles [Audiveris](https://github.com/Audiveris/audiveris),
the optical music recognition engine, unmodified, from the release tag in the
`AUDIVERIS_VERSION` build argument of the [Dockerfile](./Dockerfile) (currently 5.10.2).
`app.py` runs it as a separate command-line program.

### webmscore (GPL-3.0)

The Docker image installs [webmscore](https://github.com/LibreScore/webmscore) 1.2.1 from
npm (MuseScore compiled to WebAssembly, GPL-3.0 with MuseScore's font exception).
`mscz/to_mscz.cjs` runs it in a separate Node process to write `.mscz` files. GPL-3.0
§13 allows combining GPL-3.0 code with AGPL-3.0 code.

### Tesseract OCR (Apache-2.0)

Installed from Ubuntu packages; Audiveris uses it to read text in scores.

### solfascribe-omr (MIT)

`omr.py` is a Python port of `src/audiveris.ts` from solfascribe-omr: its handling of
multi-movement output, per-sheet salvage and failure classes.

> MIT License
>
> Copyright (c) 2026 James Eissah Aidoo
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### Sample files in docs/examples

`GeneralUser Orchestral Example` comes from GeneralUser GS and was converted to the
other formats by LibreScore's mscz-api, which this repository started from. They are
used here as test fixtures.
