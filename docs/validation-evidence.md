# Validation record, October 26, 2026

## What has actually been established

The technical problem is reproducible. A synthetic UBL invoice with a relative
attachment URI is accepted by configuration 2026-01-31 and rejected by
2026-08-31. BR-TMP-2 changes from warning to error. Both runs use the same
patched KoSIT Validator 1.6.3, with provider release-asset SHA-256 pins.
Unmodified UBL and CII fixtures are acceptance controls.

The official September 10 release note confirms BR-TMP-2 tightening and a
validator security fix. It explicitly says there are no normative changes to
the XRechnung standard in this bugfix release.

https://xeinkauf.de/aktuelles/xrechnung/xrechnung-bugfix-summer-2026/

https://github.com/itplr-kosit/validator-configuration-xrechnung/releases

An independent developer issue documents processing errors being presented
misleadingly in generated reports. The February 3, 2026 maintainer comment
identifies `processingError` in the machine report. The prototype checks for
this and for incomplete steps instead of trusting a green HTML report or
the root `valid` attribute. This issue is problem evidence, not a request to
purchase our product.

https://github.com/itplr-kosit/validator-configuration-xrechnung/issues/135

## Tests and measurable acceptance

The source includes 20 focused unit tests and one real-engine integration
test. The integration runs all three fixtures against both official packs.
Expected result: exactly one regression and one candidate rejection, with
BR-TMP-2 escalation; both control invoices accepted under both packs.
Unit coverage includes incomplete/contradictory reports, effective severity,
multiple locations for one rule, already-invalid baselines, duplicate steps,
DTD/entity rejection including UTF-16, path symlinks, ZIP traversal,
checksum tampering, timeouts and stale output invalidation.

[GitHub run 1](https://github.com/jclrk279/xrechnung-release-diff/actions/runs/40365013522) passed on October 26, 2026
for commit `b13b784bf2ce9c2786a9c350ef21a9fd71d58cf4`. It ran all 21 tests and
the composite Action smoke test, then asserted the expected regression.
This is hosted workflow evidence, not production readiness or paid demand.
No external security audit, load test or production guarantee is claimed.
The README identifies limits and non-sandboxed execution.

