"""Release gates reject incomplete or contradictory Actions artifacts."""
import io
import json
import unittest
import zipfile
from scripts.verify_delivery import validate_archive


class DeliveryEvidenceTests(unittest.TestCase):
    def fixture(self):
        files = {kind+'-ci.xml': '<testsuites><testsuite><testcase name="one"/></testsuite></testsuites>'
                 for kind in ('unit', 'postgres', 'frontend')}
        for name in ('persistence-ci.json','roles-ci.json','runtime-ci.json','observability-ci.json',
                     'accounts-ci.json','local-deployment-ci.json','order-sync-ci.json','payment-contract-ci.json'):
            files[name] = {'passed': True, 'checks': [{'passed': True}]}
        files['payment-contract-ci.json'].update(provider_sandbox_verified=False, external_payment_api_calls=0, real_refunds=0)
        files['stage3-ci.json'] = {'passed': True, 'restore': {'resume_verified': True, 'source_stopped_during_resume': True}}
        files['stage1-ci/summary.json'] = {'passed': True, 'cases': [{'passed': True} for _ in range(5)]}
        for step in ('1.5','1.6','1.7','1.8','1.9'):
            files['stage1-ci/step-'+step+'.json'] = {'passed': True}
        for name in ('evaluation_v3.json','evaluation_rag.json','rag-candidates-ci.json','rag-baseline-ci.json'):
            files[name] = {}
        return files

    def verify(self, files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            for name, value in files.items():
                z.writestr(name, value if isinstance(value, str) else json.dumps(value))
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as z:
            return validate_archive(z)

    def test_complete_evidence(self):
        counts, reports = self.verify(self.fixture())
        self.assertEqual(counts, {'unit': 1, 'postgres': 1, 'frontend': 1})
        self.assertEqual(len(reports), 10)

    def test_missing_phase_five_report(self):
        f = self.fixture(); del f['accounts-ci.json']
        with self.assertRaisesRegex(RuntimeError, 'Missing'): self.verify(f)

    def test_failed_phase_five_report(self):
        f = self.fixture(); f['order-sync-ci.json']['passed'] = False
        with self.assertRaisesRegex(RuntimeError, 'did not pass'): self.verify(f)

    def test_false_subcheck_even_with_passed_summary(self):
        f = self.fixture(); f['local-deployment-ci.json']['checks'][0]['passed'] = False
        with self.assertRaisesRegex(RuntimeError, 'subchecks'): self.verify(f)

    def test_skipped_frontend_is_not_success(self):
        f = self.fixture(); f['frontend-ci.xml'] = '<testsuite><testcase><skipped/></testcase></testsuite>'
        with self.assertRaisesRegex(RuntimeError, 'unsuccessful'): self.verify(f)

    def test_empty_junit_is_not_success(self):
        f = self.fixture(); f['unit-ci.xml'] = '<testsuite/>'
        with self.assertRaisesRegex(RuntimeError, 'missing tests'): self.verify(f)

    def test_offline_payment_cannot_claim_provider_acceptance(self):
        f = self.fixture(); f['payment-contract-ci.json']['provider_sandbox_verified'] = True
        with self.assertRaisesRegex(RuntimeError, 'incorrect scope'): self.verify(f)

    def test_ambiguous_artifact_is_rejected(self):
        f = self.fixture(); f['extra/accounts-ci.json'] = f['accounts-ci.json']
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'): self.verify(f)

    def test_missing_individual_fault_is_rejected(self):
        f = self.fixture(); del f['stage1-ci/step-1.7.json']
        with self.assertRaisesRegex(RuntimeError, 'Missing'): self.verify(f)

    def test_original_approval_recovery_required(self):
        f = self.fixture(); f['stage3-ci.json']['restore']['resume_verified'] = False
        with self.assertRaisesRegex(RuntimeError, 'approval recovery'): self.verify(f)


if __name__ == '__main__': unittest.main()
