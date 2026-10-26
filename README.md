# XRechnung Release Diff

Compare the same invoice fixture corpus against two pinned XRechnung rule
packs. Find newly blocking rules before changing your application's validator
configuration. Local deterministic processing with KoSIT Validator 1.6.3.

**Status: experimental prototype.** [GitHub CI passed](https://github.com/jclrk279/xrechnung-release-diff/actions/runs/40365013522)
on October 26, 2026: 20 unit tests, one real-engine integration test and the
composite Action smoke test. Marketplace listing is not yet published.
There is no hosted service or paid subscription.

## Reproduce a real rule change

Requirements: Linux, Python 3.10+, Java 17, and network access to official
GitHub release assets on the first run. No Python packages or API key needed.

```sh
python3 xrechnung_diff.py --input examples --output demo-output
```

The command intentionally exits **1**: `relative-attachment-ubl.xml` is
accepted by the January 2026 configuration, but rejected by the August
configuration after BR-TMP-2 changes from warning to error. The unmodified
UBL and CII samples provide acceptance controls.

Outputs: `diff.json`, `summary.md`, and `diff.sarif`. Findings contain rule IDs,
effective severity and hashes of locations, without raw validator messages.
The Markdown summary can be read locally or in the GitHub job summary.

| Exit | Meaning |
| --- | --- |
| 0 | Selected failure policy did not trigger; individual invoices may still be rejected |
| 1 | Comparison completed and selected failure policy triggered |
| 2 | Incomplete run, unsupported input, checksum failure or processing error |

Default policy `--fail-on regressions` detects new blocking findings, severity
escalations and accepted-to-rejected assessments. Use
`--fail-on candidate-rejections` to require all fixtures to be accepted by the
candidate, or `--fail-on never` for report-only mode. Incomplete runs still
exit 2 in report-only mode. Old output is invalidated before a new comparison.

## GitHub Actions

The supplied `action.yml` is a composite action for Ubuntu runners. Use the public action in your repository:

```yaml
permissions:
  contents: read
jobs:
  invoice-rules:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false
      - uses: jclrk279/xrechnung-release-diff@b13b784bf2ce9c2786a9c350ef21a9fd71d58cf4
        with:
          path: tests/invoice-fixtures
          baseline: '2026-01-31'
          candidate: '2026-08-31'
          fail-on: regressions
```

The example pins the reviewed CI-tested commit. Review updates before
changing that SHA. No issue-writing token or PR comment permissions are needed.
The MVP writes a job summary, not PR comments. It neither uploads artifacts
nor submits SARIF to GitHub code scanning automatically.

## Scope and limits

* Base XRechnung 3.0 profile, UBL Invoice/CreditNote and CII XML. PDF containers,
  extension profiles and other countries are unsupported.
* Only rule packs `2026-01-31` and `2026-08-31` are currently pinned. Both run
  on the same patched validator to isolate configuration changes.
* Maximum 100 fixtures, 2 MiB each, 20 MiB total, 120 seconds per pack by
  default, configurable up to 300. Java heap cap 512 MiB. Runner overhead and
  native memory are additional. The Action job should also have a time limit.
* Fixture ordering creates aliases. Code and hashed XPath identify a finding;
  this is not semantic invoice reconciliation. Corpus changes can change aliases.
* Network outages and GitHub asset limits fail closed. Cache uses verified
  compressed assets, rechecks hashes on every run and extracts fresh copies.
  CLI `--offline --cache PATH` supports pre-provisioned assets.
* Validation is technical. It does not guarantee recipient acceptance,
  authenticity, payment, tax treatment or legal compliance.

## Data and security

Use synthetic fixtures, especially in public repositories. Invoice XML can
contain bank details, personal data and business secrets. It is processed
inside your runner and never sent to an LLM or a hosted validation API by this
tool. Network downloads are limited by design to pinned toolchain assets;
this is not an operating-system network sandbox or a security certification.

Raw XML reports and input snapshots use a temporary directory and are removed
on normal completion. Hard runner termination can leave temporary files until
runner cleanup. Use ephemeral isolated runners for untrusted input and never
run untrusted pull-request code in a privileged `pull_request_target` job.
Persist only the sanitized reports you intend to disclose. Even hashes and
rule IDs are metadata. Input paths are excluded unless CLI `--include-paths`
is explicitly used. Filenames never appear in the default job summary.

DTD/entity declarations, external entity references, input stylesheets,
symlinks and unsupported profiles are rejected. Archive members are checked
for traversal and symlinks. Missing steps and processingError reports are
incomplete, never a passing validation. This does not replace a security audit.

## Development

```sh
python3 -m unittest discover -s tests -v
XRDIFF_INTEGRATION=1 python3 -m unittest discover -s tests -v
```

Integration tests download the pinned official assets unless already cached.
No service charge is imposed by this code. Runner and network usage may be
billable under the user's provider/account terms.

## Feedback

Tried the tool? [Report what it caught and which maintenance work matters](https://github.com/jclrk279/xrechnung-release-diff/issues/new?template=adoption-feedback.yml).
Please use synthetic examples only. The optional USD99/month maintenance
question is research about an unbuilt service, not an order or subscription.

## Project status

Free experimental software, including private-repository use. No hosted
processing service is offered. See `docs/validation-evidence.md` for the
reproduced rule change and test scope.

## License

Apache-2.0. See `LICENSE` and `THIRD_PARTY_NOTICES.md` for example provenance.
