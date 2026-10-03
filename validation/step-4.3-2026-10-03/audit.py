"""Offline review of retained demo materials, not a new business-flow test.

Run with artifact Python (Pillow, pypdf), --render-dir and a new --output path.
Rendering and visual review are separate; this script cannot certify appearance.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / 'docs/demo'
OLD = ROOT / 'validation/step-4.2-2026-09-23'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new report path')
    checks = {}
    story = read(DEMO / 'story.json')
    scenes = story['scenes']
    prior = read(OLD / 'verification.json')
    before, after, final = [read(OLD / (name + '.json')) for name in
                            ('before-approval', 'after-approval', 'final-demo')]
    md = (DEMO / 'README.md').read_text(encoding='utf-8')
    preserved = [DEMO / 'ResolveFlow-demo.docx', DEMO / 'story.json']
    preserved += sorted((DEMO / 'screenshots').glob('*.png'))
    checks['original_docx_story_and_nine_png_hashes'] = len(preserved) == 11 and all(
        digest(p) == prior['artifacts_sha256'][p.relative_to(ROOT).as_posix()] for p in preserved)
    checks['capture_date_and_project_consistent'] = story['date'] == '2026-09-23' and all(
        x['captured_at'].startswith(story['date']) and x['project'] == story['project']
        for x in (before, after, final))

    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    with ZipFile(DEMO / 'ResolveFlow-demo.docx') as z:
        xml = ET.fromstring(z.read('word/document.xml'))
        text = '\n'.join(''.join(p.itertext()) for p in xml.findall('.//w:p', ns))
        rels = {x.attrib['Id']: x.attrib['Target'] for x in
                ET.fromstring(z.read('word/_rels/document.xml.rels'))}
        images = xml.findall('.//a:blip', ns)
        checks['nine_embedded_images'] = len(images) == len(scenes) == 9
        pixel_checks = []
        for scene, embedded in zip(scenes, images):
            with Image.open(DEMO / 'screenshots' / scene['image']) as original:
                box = scene['crop']
                bounds = 0 <= box[0] < box[2] <= original.width and 0 <= box[1] < box[3] <= original.height
                cropped = original.crop(box).convert('RGB')
                target = rels[embedded.attrib['{' + ns['r'] + '}embed']]
                with Image.open(io.BytesIO(z.read(posixpath.normpath('word/' + target)))) as picture:
                    pixel_checks.append(bounds and picture.size == cropped.size and
                                        picture.convert('RGB').tobytes() == cropped.tobytes())
        checks['all_embedded_pixels_equal_declared_original_crops'] = len(pixel_checks) == 9 and all(pixel_checks)
    checks['all_scene_text_in_word_and_markdown'] = all(
        scene[field] in text and scene[field] in md for scene in scenes
        for field in ('title', 'time', 'say', 'action', 'observe'))
    final_runs = {x['id']: x for x in final['runs']}
    checks['scene_run_ids_resolve'] = all(
        x['run_id'] is None or x['run_id'] in final_runs for x in scenes)
    checks['four_expected_order_outcomes'] = sorted(
        (x['order_id'], x['status']) for x in final['runs']) == [
        ('RF-1001', 'already_refunded'), ('RF-1001', 'refunded'),
        ('RF-1002', 'auto_rejected'), ('RF-1004', 'refunded')]
    rid = scenes[2]['run_id']
    by_id = lambda report: {x['id']: x for x in report['runs']}
    checks['same_approval_run_resumed'] = (
        by_id(before)[rid]['status'] == 'awaiting_approval' and
        by_id(after)[rid]['status'] == final_runs[rid]['status'] == 'refunded')
    checkpoints = lambda report: {x['thread_id']: x['count'] for x in report['database']['checkpoints']}
    checks['approval_checkpoints_4_to_6'] = (
        checkpoints(before)[rid] == 4 and checkpoints(after)[rid] == checkpoints(final)[rid] == 6)
    approvals = final['database']['rf_approvals']
    checks['reviewer_approval_reason_matches'] = (
        len(approvals) == 1 and approvals == after['database']['rf_approvals'] and
        approvals[0]['run_id'] == rid and approvals[0]['approved'] is True and
        approvals[0]['actor'] == 'reviewer' and
        approvals[0]['reason'] == '合成演示：核对订单与政策后，批准本次例外模拟退款。')
    refunds = lambda report: sorted(report['database']['rf_refunds'], key=lambda x: x['order_id'])
    checks['before_approval_one_refund_no_approval'] = (
        len(refunds(before)) == 1 and refunds(before)[0]['order_id'] == 'RF-1001' and
        before['database']['rf_approvals'] == [])
    checks['two_refunds_expected_amounts'] = [(x['order_id'], x['amount']) for x in refunds(final)] == [
        ('RF-1001', 29900), ('RF-1004', 15900)]
    checks['full_refund_rows_preserved_after_duplicate'] = refunds(after) == refunds(final)
    checks['all_jobs_done'] = all(x['job']['status'] == 'done' for x in final['runs'])
    checks['zero_model_usage'] = all(x['mode'] == 'demo' and x['state']['usage'] == {
        'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0} for x in final['runs'])
    checks['demo_bm25_no_model_key'] = (final['database']['mode'] == 'demo' and
        final['database']['retrieval_mode'] == 'bm25' and not final['database']['model_key_present'])
    checks['historical_monitor_counts'] = (final['metrics']['attempts']['total'] == 5 and
        final['metrics']['attempts']['failed'] == 0 and final['alerts']['available'] and
        final['alerts']['workers_online'] == 1 and final['alerts']['alerts'] == [])

    pages = list(PdfReader(args.render_dir / 'ResolveFlow-demo.pdf').pages)
    checks['eleven_rendered_pages'] = len(pages) == 11 and len(list(args.render_dir.glob('page-*.png'))) == 11
    normalize = lambda value: re.sub(r'\s+', '', value)
    checks['each_scene_on_expected_word_page'] = len(pages) == 11 and all(
        normalize(scene['title']) in normalize(pages[i + 1].extract_text()) for i, scene in enumerate(scenes))
    broken = []
    links = 0
    for path in DEMO.glob('*.md'):
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            links += 1
            if not (path.parent / target.split('#')[0]).exists():
                broken.append(path.name + ':' + target)
    checks['demo_local_links_exist'] = links > 0 and not broken
    baseline = read(ROOT / 'validation/step-4.1-2026-09-23/verification.json')['source_sha256_normalized_crlf']
    checks['runtime_sources_unchanged'] = all(
        hashlib.sha256((ROOT / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == h
        for p, h in baseline.items())

    guards = []
    cases = [
        ('reject_main_project', 'scripts/demo_environment.py', ['start', '--project', 'resolveflow'], 'dedicated'),
        ('reject_main_port', 'scripts/demo_environment.py', ['start', '--project', 'resolveflow-qa-demo-guard', '--port', '8003'], 'non-main port'),
        ('reject_low_port', 'scripts/demo_environment.py', ['start', '--project', 'resolveflow-qa-demo-guard', '--port', '80'], 'non-main port'),
        ('reject_high_port', 'scripts/demo_environment.py', ['start', '--project', 'resolveflow-qa-demo-guard', '--port', '65536'], 'non-main port'),
        ('evidence_reject_main', 'scripts/demo_evidence.py', ['--project', 'resolveflow', '--output', 'work/step43/unused.json'], 'dedicated'),
        ('evidence_reject_existing', 'scripts/demo_evidence.py', ['--project', 'resolveflow-qa-demo-guard', '--output', str(OLD / 'final-demo.json')], 'new evidence path'),
    ]
    for name, script, params, expected in cases:
        result = subprocess.run([sys.executable, script, *params], cwd=ROOT, capture_output=True, timeout=30)
        checks[name] = result.returncode == 2 and expected.encode() in result.stderr
        guards.append({'name': name, 'exit_code': result.returncode, 'expected_rejection': checks[name]})
    protected = [ROOT / 'scripts/demo_environment.py', ROOT / 'scripts/demo_evidence.py']
    protected += sorted(OLD.glob('*.json'))
    checks['historical_json_and_operation_helpers_unchanged'] = all(
        subprocess.check_output(['git', 'show', 'f98ba14:' + p.relative_to(ROOT).as_posix()], cwd=ROOT).replace(b'\r\n', b'\n') ==
        p.read_bytes().replace(b'\r\n', b'\n') for p in protected)
    report = {
        'step': '4.3', 'checked_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'offline material and retained evidence review; not fresh runtime validation',
        'passed': all(checks.values()), 'checks': checks, 'guard_checks': guards,
        'local_links_checked': links, 'broken_links': broken, 'runtime_sources_checked': len(baseline),
        'artifacts_sha256': {p.relative_to(ROOT).as_posix(): digest(p) for p in preserved + protected},
        'render_page_sha256': {p.name: digest(p) for p in sorted(args.render_dir.glob('page-*.png'))},
        'visual_review': 'See visual-review.json; not inferred by this script',
        'fresh_business_replay': False, 'database_or_container_mutations': False,
        'pushed': False, 'new_ci': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'passed': report['passed'], 'checks': len(checks),
                      'failed': [k for k, v in checks.items() if not v]}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
