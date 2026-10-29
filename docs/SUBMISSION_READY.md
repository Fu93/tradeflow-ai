# Submission handoff — 29 October 2026

## Submission and judging times

[Official rules](https://nebiusglobalaihackathon.devpost.com/rules), checked 29 October:

- Submit before **30 October 2026, 17:00 London / UTC** (10:00 PDT; 31 October 01:00 Taiwan).
- Keep the demo and judge credential usable through **15 December 2026, 20:00 London / UTC** (12:00 PST).
- Use a public YouTube video **under three minutes** and English description, captions and testing instructions.

## Prepared

- [Concise English Devpost copy](DEVPOST_FINAL.md); [detailed evidence and original live observations](DEVPOST_SUBMISSION.md).
- [Tool feedback](NEBIUS_NVIDIA_FEEDBACK.md), MIT license, runtime integration documentation.
- Risk feasibility and final-money checks, nonzero-duty inverse, active supplier ranks, and per-attempt retry accounting.
- Confirmation queue before supplier cards; optional landed inputs collapsed; next-step guidance and automatic scrolling.
- `scripts/record_submission_demo.py` records the real local UI and generates English captions and narration with FFmpeg's built-in Flite voice. It creates a local MP4, caption file, and desktop/mobile screenshots. The recording is explicitly **offline**; it makes no paid model calls and must not be presented as a fresh Nemotron or Tavily run.

## Recorded walkthrough

[Download the 2:02 MP4](demo/tradeflow-demo.mp4) · [English captions](demo/captions.srt) · [Narration](demo/narration.txt) · [Recording evidence](demo/recording-evidence.json). English narration is synthesized with Flite. The recorded audit verified successfully and the approved draft was not transmitted.

## Generate the walkthrough

Install development requirements and Playwright Chromium. FFmpeg must include `flite` and `subtitles` filters.

```bash
python -m playwright install chromium
python scripts/record_submission_demo.py --out /tmp/tradeflow-video
```

The script uses a temporary database and a local-only test credential, disables providers and SMTP, checks the sample and a second supplier, switches to the solver's push figure, approves without transmitting, and verifies the audit. It does not read your `.env`. Outputs include `tradeflow-demo.mp4`, `captions.srt`, `narration.txt` and `recording-evidence.json`.

For the strongest final entry, replace the offline-reader portion with a short genuine live Nemotron/Tavily capture when the demo credential is available. Keep the recording's labels honest; do not overlay made-up inference results. The existing repository live-call observations can be cited as historical evidence.

## Remaining account actions

1. Review the generated MP4; upload it as **Public** to your YouTube account. Put its URL in Devpost and at the top of the README. No public video URL exists until that upload is complete.
2. Paste DEVPOST_FINAL into the submission; enter the existing demo credential privately. Do not commit the credential.
3. After deploying the reviewed changes, check `/version`, `/ready`, and the full sample workflow in a fresh browser. Confirm one genuine model read and one Tavily lookup if balance and credentials permit. The local recording does not prove live-provider availability.
4. Keep the service and inference budget available through judging. The model cap defaults to 40 **API attempts** per UTC day per process, including retries; in-memory accounting resets on restart. Select an affordable judging budget, monitor `/health`, and avoid rotating the judge key early.
5. Click Devpost's final Submit control before the deadline and retain the confirmation. A prepared repository or draft form is not a submitted entry.

These account actions are not marked complete merely because their instructions or assets exist.
