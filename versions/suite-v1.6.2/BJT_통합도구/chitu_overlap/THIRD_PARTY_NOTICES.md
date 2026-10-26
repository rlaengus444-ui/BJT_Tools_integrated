# CHITUBOX format references

The native project reader was implemented from the structure documented by
Open Resin Alliance's `df-plugin-chitubox` interoperability project:

- https://github.com/Open-Resin-Alliance/df-plugin-chitubox/blob/main/CbxParser.ts
- https://github.com/Open-Resin-Alliance/df-plugin-chitubox/blob/main/CbxLayout.test.ts
- https://github.com/Open-Resin-Alliance/df-plugin-chitubox/blob/main/fileTypeHandlers.ts

The reader is an independent Python implementation of the declared-pointer
layout. It does not include the reference parser's geometry guessing,
support reconstruction, or best-effort fallbacks. The upstream license is
reproduced here for attribution.

MIT License

Copyright (c) 2026 Open Resin Alliance

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
