# 871337 Labs: raw artifacts

Implementation and artifact layer for [labs.871337.xyz](https://labs.871337.xyz).

The publishing flow is:

```text
LinkedIn  ->  871337 Labs  ->  this repository
```

- **LinkedIn** carries the narrative and the decision. It stands on its own.
- **Labs** carries the technical analysis: methodology, evidence, failures, tradeoffs, and reasoning.
- **This repository** carries the receipts: reports, raw measurements, harnesses, configurations, and logs.

Each layer is a complete stopping point. This one exists for the reader who wants to verify or reproduce a result, not for the reader who just wants the answer.

## Structure

One directory per article, named with the article slug:

```text
<article-slug>/
├── README.md      index of what is in the folder
├── REPORT.md      the full engineering report
├── APPENDIX-*.md  focused comparisons
└── ...            harnesses, configs, raw results
```

## Why the reports live here rather than on Labs

An article should carry the principle and the reasoning. It should not reproduce a full engagement report, because that buries the finding. These documents are the long form the article links to.

The material here is also deliberately dated. Figures are correct for the hardware, model revision, and engine version recorded in each document, and are expected to age. Read the environment section of a report before reusing any number from it.

## Sanitization

Everything here is sanitized before publication. Host names, LAN addresses, endpoint URLs, credentials, and access details are removed or replaced with placeholders such as `<host-ip>`. Architecture is preserved, because that is what makes a result reproducible. Identifiers are not, because they tell a reader nothing.

Measured values are never altered. If a number appears in a sanitized copy, it is the number that was recorded.
