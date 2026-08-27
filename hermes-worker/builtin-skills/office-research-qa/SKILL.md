---
name: office-research-qa
description: Research-first creation and rigorous visual QA for professional Word, PowerPoint, and PDF deliverables. Use for any request to create, rewrite, edit, or design DOCX, PPTX, PPT, PDF, reports, proposals, briefs, manuals, white papers, or presentation decks.
license: MIT
metadata:
  hermes:
    version: 1.0.0
    tags: [office, research, quality, docx, pptx, pdf, visual-review]
    category: productivity
    related_skills: [docx, pdf, pptx, codex-ppt]
---

# Office Research and QA

Use this skill as the controller for Office deliverables. Load the format-specific skill as well: `docx`, `pdf`, `pptx`, or `codex-ppt`.

## Required workflow

1. Decompose the request into audience, goal, claims, deliverables, source constraints, visual direction, and acceptance checks. Save the decomposition and a checklist in the project directory.
2. Before drafting, search the web with the browser. Prefer primary or authoritative sources, record URLs, titles, dates, and which claim each source supports. Treat prior model knowledge as unverified until checked. If a user-provided source is sufficient, inspect it first and search only to fill genuine gaps.
3. Scan the installed Skill and workflow indexes twice: once before research and once after the evidence is collected. Reuse relevant capabilities even when they were created for another file format.
4. Write a final content and design plan. Define the hierarchy, page or slide roles, visual motif, palette, typography, asset needs, and a concrete highlight for every page or slide.
5. Build with the format skill. Use `pptx` for editable objects. Use `codex-ppt` for image-based slides only after its image backend capability check succeeds and the user does not require editable slide elements.
6. Validate the package structurally and semantically. Run `document-tool verify OUTPUT` plus the format skill's own read/validate command. Treat either failure as a failed build.
7. Render every page or slide to a separate PNG. Inspect every image for clipped or overlapping text, weak hierarchy, low contrast, empty space, repeated layouts, inconsistent assets, and illegible typography. A montage alone is insufficient.
8. Save `quality-submission.json` beside the deliverable using the schema in `references/quality-submission.md`. List every final preview. The backend sends each image to an independent blank-context reviewer; do not self-award the final score.
9. Emit artifact markers for the deliverable, `quality-submission.json`, the source report, and every final page or slide preview. Do not finish while any marker points to a missing or empty file.

## Rework

When the independent reviewer rejects the result, read its defects and required fixes, revise the source and deliverable, rerender all affected pages, and replace the corresponding preview files. Preserve good parts but do not argue with a failed score. The backend decides whether the threshold was met.

## Final response

State what was delivered, cite the research sources used, and list the concrete highlights that survived independent review. Do not claim that an output passed unless the backend review says it passed.
