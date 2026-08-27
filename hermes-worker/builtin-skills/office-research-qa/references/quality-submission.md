# Quality Submission Schema

Write UTF-8 JSON with this shape:

```json
{
  "request_summary": "What the user asked for",
  "deliverables": ["/workspace/project/final.docx"],
  "source_report": "/workspace/project/research-sources.md",
  "previews": [
    {
      "page": 1,
      "path": "/workspace/project/previews/page-01.png",
      "role": "Cover",
      "intended_highlight": "Specific visual or content highlight",
      "asset_fit": "Why the chosen assets support this page"
    }
  ],
  "checks": {
    "package_verified": true,
    "content_re_read": true,
    "all_pages_rendered": true,
    "no_clipping_or_overlap": true,
    "sources_recorded": true
  }
}
```

Requirements:

- `previews` must contain one item for every final page or slide, in order.
- Paths must be absolute `/workspace/...` paths and must name non-empty files.
- `intended_highlight` must be specific to that page. Repeated generic praise is invalid.
- Set a check to `false` when it was not actually performed. Never fabricate a passing check.
