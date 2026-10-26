#!/usr/bin/env python3
"""Local, deterministic comparison of two pinned XRechnung rule packs."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zipfile

VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent
REP = "http://www.xoev.de/de/validator/varl/1"
SCENARIOS = "http://www.xoev.de/de/validator/framework/1/scenarios"
PROFILE = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
SUPPORTED_ROOTS = {
    "{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice",
    "{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote",
    "{" + RSM + "}CrossIndustryInvoice",
}
LEVELS = {"information": 1, "warning": 2, "error": 3, "fatal": 4}
SAFE_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,120}\Z")
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 100
MAX_REPORT = 32 * 1024 * 1024


class ToolError(Exception):
    """An incomplete run must never be classified as a passing comparison."""


@dataclass(frozen=True, order=True)
class Finding:
    code: str
    location_hash: str
    level: str


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_xml(data: bytes) -> None:
    """Reject DTD/entity declarations and excessive depth before Java sees XML."""
    parser = expat.ParserCreate()
    depth = 0

    def forbidden(*_):
        raise ToolError("DTD, entity declarations and external entities are unsupported")

    def start(_name, attrs):
        nonlocal depth
        depth += 1
        if depth > 128 or len(attrs) > 128:
            raise ToolError("XML complexity limit exceeded")

    def end(_name):
        nonlocal depth
        depth -= 1

    def pi(target, _data):
        if target.lower() == "xml-stylesheet":
            raise ToolError("Input stylesheets are unsupported")

    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.ProcessingInstructionHandler = pi
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise ToolError("An input is not well-formed XML") from error


def validate_input(data: bytes) -> None:
    if len(data) > MAX_FILE:
        raise ToolError("Input exceeds the 2 MiB per-file limit")
    check_xml(data)
    root = ET.fromstring(data)
    if root.tag not in SUPPORTED_ROOTS:
        raise ToolError("Only UBL Invoice/CreditNote and CII XRechnung XML are supported")
    if root.tag == "{" + RSM + "}CrossIndustryInvoice":
        profile = root.find("{" + RSM + "}ExchangedDocumentContext/{" + RAM + "}GuidelineSpecifiedDocumentContextParameter/{" + RAM + "}ID")
    else:
        profile = root.find("{" + CBC + "}CustomizationID")
    if profile is None or (profile.text or "").strip() != PROFILE:
        raise ToolError("Only the base XRechnung 3.0 profile is supported")


def collect_inputs(source: Path) -> list[tuple[Path, bytes]]:
    if source.is_symlink() or not source.exists():
        raise ToolError("Input path is missing or is a symlink")
    if source.is_file():
        paths = [source]
    elif source.is_dir():
        paths = []
        for directory, dirs, files in os.walk(source, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise ToolError("Symlink directories are unsupported")
            for name in sorted(files):
                p = Path(directory) / name
                if p.is_symlink():
                    raise ToolError("Symlink files are unsupported")
                if p.suffix.lower() == ".xml":
                    paths.append(p)
    else:
        raise ToolError("Input must be a regular XML file or directory")
    paths = sorted(paths)
    if not paths or len(paths) > MAX_FILES:
        raise ToolError("Provide between 1 and 100 XML files")
    result, total = [], 0
    for p in paths:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(p, flags)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ToolError("Input must be a regular file")
                data = stream.read(MAX_FILE + 1)
        except OSError as error:
            raise ToolError("Unable to safely read an input file") from error
        validate_input(data)
        total += len(data)
        if total > MAX_TOTAL:
            raise ToolError("Corpus exceeds the 20 MiB total limit")
        result.append((p, data))
    return result


def verified_asset(asset: dict, target: Path, offline: bool = False) -> bytes:
    if target.is_symlink():
        raise ToolError("Symlink cache entries are unsupported")
    if target.exists():
        data = target.read_bytes()
        if digest(data) != asset["sha256"]:
            raise ToolError("Cached toolchain checksum mismatch; remove that cache entry")
        return data
    if offline:
        raise ToolError("Pinned toolchain asset is unavailable in offline cache")
    last_error = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(asset["url"], headers={"User-Agent": "xrechnung-release-diff/" + VERSION})
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024 or digest(data) != asset["sha256"]:
                raise ToolError("Downloaded toolchain failed its pinned checksum")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temp = Path(stream.name)
                stream.write(data)
            temp.replace(target)
            return data
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    raise ToolError("Unable to download pinned toolchain; retry later or use offline cache") from last_error


def safe_extract(data: bytes, destination: Path) -> None:
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 1500 or sum(x.file_size for x in entries) > 32 * 1024 * 1024:
                raise ToolError("Configuration archive exceeds limits")
            seen = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                        or ":" in entry.filename or entry.filename in seen
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise ToolError("Unsafe configuration archive member")
                seen.add(entry.filename)
                out = destination.joinpath(*path.parts)
                if entry.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(archive.read(entry))
    except zipfile.BadZipFile as error:
        raise ToolError("Configuration archive is invalid") from error


def parse_report(data: bytes) -> dict:
    if len(data) > MAX_REPORT:
        raise ToolError("Validator report exceeds limit")
    check_xml(data)
    root = ET.fromstring(data)
    if root.tag != "{" + REP + "}report":
        raise ToolError("Unexpected validator report format")
    if root.findtext("{" + REP + "}engine/{" + REP + "}name") != "KoSIT Validator 1.6.3":
        raise ToolError("Unexpected validator engine")
    # @valid is not the acceptance decision: warnings may set it false while
    # the official scenario accepts the invoice. Never infer success from it.
    assessment = root.find("{" + REP + "}assessment")
    if assessment is None or len(assessment) != 1:
        raise ToolError("Validator acceptance assessment is absent or ambiguous")
    outcome = assessment[0].tag
    if outcome not in {"{" + REP + "}accept", "{" + REP + "}reject"}:
        raise ToolError("Validator returned an indeterminate assessment")
    matched = root.find("{" + REP + "}scenarioMatched")
    if matched is None:
        raise ToolError("No supported validation scenario matched")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in {"processingError", "processingErrors"}:
            raise ToolError("Validator reported a processing failure")
    step_nodes = matched.findall("{" + REP + "}validationStepResult")
    steps = {x.get("id"): x for x in step_nodes}
    if len(steps) != len(step_nodes) or None in steps:
        raise ToolError("Ambiguous validator step results")
    required = {"val-xml", "val-xsd"}
    if "val-xsd" in steps and steps["val-xsd"].get("valid") == "true":
        required |= {"val-sch.1", "val-sch.2"}
    if not required.issubset(steps):
        raise ToolError("Validator report is incomplete")
    findings = []
    for step in steps.values():
        if step.get("valid") not in {"true", "false"}:
            raise ToolError("Validator step has no definitive result")
        messages = step.findall("{" + REP + "}message")
        if step.get("valid") == "false" and not messages:
            raise ToolError("Failed validation step has no findings; processing may be incomplete")
        for message in messages:
            level = message.get("level", "")
            if level not in LEVELS:
                raise ToolError("Unrecognized effective finding severity")
            code = message.get("code") or "UNCLASSIFIED"
            if not SAFE_CODE.fullmatch(code):
                raise ToolError("Unrecognized finding code format")
            location = message.get("xpathLocation", "")
            findings.append(Finding(code, digest(location.encode()), level))
    if steps["val-xml"].get("valid") != "true":
        raise ToolError("XML processing did not complete")
    if outcome.endswith("}accept") and any(LEVELS[f.level] >= 3 for f in findings):
        raise ToolError("Acceptance assessment contradicts blocking findings")
    return {"accepted": outcome.endswith("}accept"), "findings": sorted(set(findings))}


def compare_results(baseline: dict, candidate: dict) -> dict:
    def strongest(findings):
        result = {}
        for finding in findings:
            key = (finding.code, finding.location_hash)
            if key not in result or LEVELS[finding.level] > LEVELS[result[key].level]:
                result[key] = finding
        return result
    old = strongest(baseline["findings"])
    new = strongest(candidate["findings"])
    introduced, escalated, resolved = [], [], []
    for key, finding in new.items():
        prior = old.get(key)
        if prior is None:
            introduced.append(finding)
        elif LEVELS[finding.level] > LEVELS[prior.level]:
            escalated.append({"code": finding.code, "location_hash": finding.location_hash,
                              "from": prior.level, "to": finding.level})
    for key, finding in old.items():
        current = new.get(key)
        if current is None or LEVELS[current.level] < LEVELS[finding.level]:
            resolved.append(finding)
    blocking = [x for x in introduced if LEVELS[x.level] >= 3]
    blocking += [Finding(x["code"], x["location_hash"], x["to"]) for x in escalated
                 if LEVELS[x["to"]] >= 3]
    regressed = bool(blocking) or (baseline["accepted"] and not candidate["accepted"])
    return {"baseline_accepted": baseline["accepted"], "candidate_accepted": candidate["accepted"],
            "regression": regressed, "introduced": [asdict(x) for x in sorted(introduced)],
            "escalated": sorted(escalated, key=lambda x: (x["code"], x["location_hash"])),
            "resolved": [asdict(x) for x in sorted(resolved)],
            "blocking_changes": [asdict(x) for x in sorted(set(blocking))]}


def run_validator(jar: Path, config: Path, inputs: list[Path], out: Path, timeout: int) -> dict:
    out.mkdir()
    command = ["java", "-Xmx512m", "-Djavax.xml.accessExternalDTD=",
               "-Djavax.xml.accessExternalSchema=file,jar", "-Djavax.xml.accessExternalStylesheet=file,jar",
               "-jar", str(jar), "-s", str(config / "scenarios.xml"), "-r", str(config),
               "-o", str(out), *map(str, inputs)]
    try:
        completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ToolError("Validator time limit exceeded; comparison is incomplete") from error
    except OSError as error:
        raise ToolError("Java is unavailable; install a supported JRE") from error
    if completed.returncode not in {0, 1}:
        raise ToolError("Validator process failed; comparison is incomplete")
    results = {}
    for source in inputs:
        report = out / (source.stem + "-report.xml")
        if not report.is_file() or report.stat().st_size > MAX_REPORT:
            raise ToolError("Validator did not produce a complete bounded report")
        results[source.name] = parse_report(report.read_bytes())
    return results


def summary(result: dict) -> str:
    lines = ["## XRechnung Release Diff", "", "Local comparison: **" + result["baseline"] + " → " + result["candidate"] + "**.", "",
             "| Fixture | Baseline accepted | Candidate accepted | New blocking findings |",
             "|---|---|---|---|"]
    for item in result["files"]:
        codes = sorted({x["code"] for x in item["blocking_changes"]})
        lines.append("| " + item["label"] + " | " + str(item["baseline_accepted"]).lower() + " | "
                     + str(item["candidate_accepted"]).lower() + " | " + (", ".join(codes) or "none") + " |")
    lines += ["", "Regressions: **" + str(result["regressions"]) + "**. Candidate rejections: **" + str(result["candidate_rejections"]) + "**.", "",
              "A zero-regression result does not mean every invoice is accepted. This compares pinned technical rules; recipient acceptance and legal/tax validity are outside scope.",
              "", "No invoice text, values or raw validator messages are included. Filenames are hidden in this summary.", ""]
    return "\n".join(lines)


def sarif(result: dict) -> dict:
    findings = []
    for item in result["files"]:
        for finding in item["blocking_changes"]:
            findings.append({"ruleId": finding["code"], "level": "error",
                             "message": {"text": item["label"] + ": a blocking rule was introduced or escalated."},
                             "partialFingerprints": {"xrechnung/v1": digest((item["sha256"] + finding["code"] + finding["location_hash"]).encode())}})
        if item["regression"] and not item["blocking_changes"]:
            findings.append({"ruleId": "ASSESSMENT-REGRESSION", "level": "error",
                             "message": {"text": item["label"] + ": candidate rejects a previously accepted fixture."}})
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{"tool": {"driver": {"name": "XRechnung Release Diff", "version": VERSION}}, "results": findings}]}


def execute(args) -> dict:
    # Invalidate previous outputs before input checks or downloads. Consumers
    # must never read yesterday's passing JSON after an incomplete new run.
    out = Path(args.output)
    if out.is_symlink():
        raise ToolError("Symlink output directories are unsupported")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name in ("diff.json", "summary.md", "diff.sarif"):
        target = out / name
        if target.is_symlink():
            raise ToolError("Symlink output files are unsupported")
    for name in ("diff.json", "summary.md", "diff.sarif"):
        (out / name).unlink(missing_ok=True)
    (out / "diff.json").write_text(json.dumps({"schema_version": 1, "status": "incomplete"}) + "\n")
    (out / "summary.md").write_text("## XRechnung Release Diff\n\nINCOMPLETE: no comparison result is available.\n")
    lock = json.loads((ROOT / "toolchain.lock.json").read_text())
    if args.baseline not in lock["configurations"] or args.candidate not in lock["configurations"]:
        raise ToolError("Select a supported pinned rule-pack version")
    inputs = collect_inputs(Path(args.input))
    cache = Path(args.cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    jar_data = verified_asset(lock["validator"], cache / "validator.jar", args.offline)
    configs = {version: verified_asset(lock["configurations"][version], cache / (version + ".zip"), args.offline)
               for version in {args.baseline, args.candidate}}
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="xrdiff-") as temporary:
        work = Path(temporary)
        jar = work / "validator.jar"
        jar.write_bytes(jar_data)
        snapshots = []
        for index, (_, data) in enumerate(inputs, 1):
            snapshot = work / ("fixture-" + str(index).zfill(4) + ".xml")
            snapshot.write_bytes(data)
            snapshots.append(snapshot)
        for version, data in configs.items():
            safe_extract(data, work / version)
        baseline = run_validator(jar, work / args.baseline, snapshots, work / "baseline-reports", args.timeout)
        candidate = run_validator(jar, work / args.candidate, snapshots, work / "candidate-reports", args.timeout)
        files = []
        for index, ((path, data), snapshot) in enumerate(zip(inputs, snapshots), 1):
            entry = {"label": "fixture-" + str(index).zfill(4), "sha256": digest(data),
                     **compare_results(baseline[snapshot.name], candidate[snapshot.name])}
            if args.include_paths:
                entry["input_path"] = str(path)
            files.append(entry)
        result = {"schema_version": 1, "tool_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                  "status": "complete", "baseline": args.baseline, "candidate": args.candidate,
                  "validator": lock["validator"], "rule_packs": {v: lock["configurations"][v] for v in sorted(configs)},
                  "files": files, "regressions": sum(x["regression"] for x in files),
                  "candidate_rejections": sum(not x["candidate_accepted"] for x in files),
                  "duration_seconds": round(time.monotonic() - start, 3)}
        for name, content in [("diff.json", json.dumps(result, indent=2)), ("summary.md", summary(result)),
                              ("diff.sarif", json.dumps(sarif(result), indent=2))]:
            target = out / name
            if target.is_symlink():
                raise ToolError("Symlink output files are unsupported")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out, delete=False) as stream:
                stream.write(content + "\n")
                staged = Path(stream.name)
            staged.replace(target)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(summary(result))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                stream.write("regressions=" + str(result["regressions"]) + "\ncandidate-rejections=" + str(result["candidate_rejections"]) + "\n")
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=os.environ.get("XRDIFF_INPUT", "examples"))
    parser.add_argument("--baseline", default=os.environ.get("XRDIFF_BASELINE", "2026-01-31"))
    parser.add_argument("--candidate", default=os.environ.get("XRDIFF_CANDIDATE", "2026-08-31"))
    parser.add_argument("--output", default=os.environ.get("XRDIFF_OUTPUT", "xrechnung-diff-results"))
    parser.add_argument("--cache", default=os.environ.get("XRDIFF_CACHE", str(ROOT / ".xrdiff-cache")))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--fail-on", choices=["regressions", "candidate-rejections", "never"], default=os.environ.get("XRDIFF_FAIL_ON", "regressions"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--include-paths", action="store_true", help="Include identifying input paths in local JSON only")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 300:
        parser.error("timeout must be between 1 and 300 seconds per rule pack")
    try:
        result = execute(args)
    except (ToolError, OSError, ValueError, ET.ParseError) as error:
        # No raw Java logs or invoice diagnostics are printed.
        message = str(error) if isinstance(error, ToolError) else "Local execution or configuration error"
        print("XRechnung Release Diff: INCOMPLETE. " + message, file=sys.stderr)
        return 2
    print("Compared " + str(len(result["files"])) + " fixtures; regressions=" + str(result["regressions"])
          + "; candidate_rejections=" + str(result["candidate_rejections"]))
    if args.fail_on == "never":
        return 0
    return int(bool(result["regressions"] if args.fail_on == "regressions" else result["candidate_rejections"]))


if __name__ == "__main__":
    sys.exit(main())
