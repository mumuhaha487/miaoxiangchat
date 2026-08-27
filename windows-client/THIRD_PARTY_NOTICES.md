# Third-party notices

This client contains implementations derived from Enikk commit
`3d7a3f0de675293f8d67b013595ef4ac4b94f35e`.

Enikk is distributed under the MIT License:

```text
MIT License

Copyright (c) 2026 Enikk Contributors

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

The Enikk repository's bundled OmniParser/YOLOv8 icon weights are deliberately
not included. Their embedded license differs from the repository README. This
client uses Windows UI Automation and RapidOCR, with a vision-model fallback.

RapidOCR source code is Apache-2.0 licensed. OCR model files distributed by the
RapidOCR package retain their respective upstream model notices.

The executable redistributes Microsoft Visual C++ Runtime files from the
Microsoft Visual C++ Redistributable (version 14.44.35211.0). They are included
under the applicable Microsoft Visual Studio license terms and are pinned so
ONNX Runtime behaves consistently across supported Windows 10/11 machines.
