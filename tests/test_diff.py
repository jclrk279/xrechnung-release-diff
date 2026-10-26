import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import xrechnung_diff as x


def report(outcome="accept", level="warning", location="/Invoice/SECRET", **kw):
    steps = ''.join('<r:validationStepResult id="' + i + '" valid="true"/>'
                    for i in ['val-xml', 'val-xsd', 'val-sch.1'])
    return ('<r:report xmlns:r="' + x.REP + '" valid="false">'
            '<r:engine><r:name>KoSIT Validator 1.6.3</r:name></r:engine>'
            '<r:scenarioMatched>' + steps + '<r:validationStepResult id="val-sch.2" valid="false">'
            '<r:message code="BR-TMP-2" level="' + level + '" xpathLocation="' + location + '">'
            'PRIVATE BANK ACCOUNT</r:message></r:validationStepResult></r:scenarioMatched>'
            '<r:assessment><r:' + outcome + '/></r:assessment></r:report>').encode()


class CoreTests(unittest.TestCase):
    def test_official_assessment_not_root_valid(self):
        self.assertTrue(x.parse_report(report())["accepted"])

    def test_effective_severity_escalation(self):
        result = x.compare_results(x.parse_report(report()), x.parse_report(report("reject", "error")))
        self.assertTrue(result['regression'])
        self.assertEqual(result['escalated'][0]['from'], 'warning')

    def test_new_occurrence_even_if_already_rejected(self):
        result = x.compare_results(x.parse_report(report('reject', 'error')),
                                   x.parse_report(report('reject', 'error', '/Invoice/OTHER')))
        self.assertTrue(result['regression'])

    def test_unchanged_rejection_is_not_regression(self):
        r = x.parse_report(report('reject', 'error'))
        self.assertFalse(x.compare_results(r, r)['regression'])

    def test_duplicate_severities_use_strongest(self):
        f = [x.Finding('RULE', 'hash', 'error'), x.Finding('RULE', 'hash', 'warning')]
        a = {'accepted': False, 'findings': f}
        b = {'accepted': False, 'findings': f[:1]}
        self.assertFalse(x.compare_results(a, b)['regression'])

    def test_processing_error_never_passes(self):
        r = report().replace(b'</r:report>', b'<processingError/></r:report>')
        with self.assertRaises(x.ToolError): x.parse_report(r)

    def test_missing_step_never_passes(self):
        r = report().replace(b'<r:validationStepResult id="val-sch.1" valid="true"/>', b'')
        with self.assertRaises(x.ToolError): x.parse_report(r)

    def test_unknown_severity_never_passes(self):
        with self.assertRaises(x.ToolError): x.parse_report(report(level='maybe'))

    def test_duplicate_step_never_passes(self):
        r = report().replace(b'</r:scenarioMatched>', b'<r:validationStepResult id="val-xml" valid="true"/></r:scenarioMatched>')
        with self.assertRaises(x.ToolError): x.parse_report(r)

    def test_contradiction_never_passes(self):
        with self.assertRaises(x.ToolError): x.parse_report(report(level='error'))

    def test_no_raw_messages_or_locations(self):
        parsed = x.parse_report(report())
        text = json.dumps([f.__dict__ for f in parsed['findings']])
        self.assertNotIn('PRIVATE', text)
        self.assertNotIn('SECRET', text)

    def test_dtd_entities_utf8_and_utf16(self):
        malicious = '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
        for encoding in ('utf-8', 'utf-16'):
            with self.assertRaises(x.ToolError): x.check_xml(malicious.encode(encoding))

    def test_stylesheet_rejected(self):
        with self.assertRaises(x.ToolError): x.check_xml(b'<?xml-stylesheet href="https://evil"?><a/>')

    def test_depth_and_malformed(self):
        for data in (b'<a>' * 129 + b'</a>' * 129, b'<a>'):
            with self.assertRaises(x.ToolError): x.check_xml(data)

    def test_input_limits_and_profile(self):
        for data in (b'x' * (x.MAX_FILE + 1), b'<a/>'):
            with self.assertRaises(x.ToolError): x.validate_input(data)

    def test_empty_and_symlink_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            with self.assertRaises(x.ToolError): x.collect_inputs(p)
            (p/'a.xml').symlink_to(x.ROOT/'examples/valid-ubl.xml')
            with self.assertRaises(x.ToolError): x.collect_inputs(p)

    def test_cached_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'asset'; p.write_bytes(b'bad')
            with self.assertRaises(x.ToolError):
                x.verified_asset({'sha256': x.digest(b'good')}, p, True)

    def test_zip_traversal_and_symlinks(self):
        for name in ('../escape', '/absolute', 'C:escape', 'a\\b'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w') as z: z.writestr(name, 'bad')
                with self.assertRaises(x.ToolError): x.safe_extract(stream.getvalue(), Path(d))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            info = zipfile.ZipInfo('link'); info.external_attr = 0o120777 << 16
            z.writestr(info, 'target')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(x.ToolError): x.safe_extract(stream.getvalue(), Path(d))

    def test_timeout_is_incomplete(self):
        with tempfile.TemporaryDirectory() as d, patch('subprocess.run', side_effect=subprocess.TimeoutExpired('java', 1)):
            with self.assertRaises(x.ToolError):
                x.run_validator(Path('x'), Path('x'), [], Path(d)/'out', 1)

    def test_failed_run_invalidates_old_green_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'diff.json').write_text('{"status":"complete","regressions":0}')
            (p/'diff.sarif').write_text('old')
            code = x.main(['--input', str(p/'missing'), '--output', d, '--offline'])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads((p/'diff.json').read_text())['status'], 'incomplete')
            self.assertFalse((p/'diff.sarif').exists())


@unittest.skipUnless(os.environ.get('XRDIFF_INTEGRATION') == '1', 'set XRDIFF_INTEGRATION=1 for real pinned KoSIT runs')
class IntegrationTests(unittest.TestCase):
    def test_real_release_regression(self):
        with tempfile.TemporaryDirectory() as d:
            code = x.main(['--input', str(x.ROOT/'examples'), '--output', d,
                           '--cache', os.environ.get('XRDIFF_TEST_CACHE', str(x.ROOT/'.xrdiff-cache'))])
            result = json.loads((Path(d)/'diff.json').read_text())
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['regressions'], 1)
            self.assertEqual(result['candidate_rejections'], 1)
            self.assertEqual(sum(f['baseline_accepted'] and f['candidate_accepted'] for f in result['files']), len(result['files']) - 1)
            changed = next(f for f in result['files'] if f['regression'])
            self.assertEqual(changed['escalated'][0]['code'], 'BR-TMP-2')
            self.assertNotIn('input_path', changed)


if __name__ == '__main__': unittest.main()
