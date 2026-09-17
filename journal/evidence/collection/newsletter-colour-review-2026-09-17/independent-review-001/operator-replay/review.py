"""Offline exact-body review; never a runtime general colour interpreter."""
import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PageObject, PdfReader
from pypdf.generic import ContentStream, NameObject
from swingset.sources.base import ParseContext
from swingset.sources.wsdc_newsletter.adapter import EventsPage
from swingset.sources.wsdc_newsletter.colour_review import reviewed_colours


def sha(body):
    return hashlib.sha256(body).hexdigest()


def direct_text_colour(page, reader):
    """Read effective nonstroking colour at text-show operators, not delayed callbacks."""
    fill = None
    stack = []
    block = None
    colours = []
    matrix = None
    start = None
    selected = []
    operations = ContentStream(page.get_contents(), reader, "bytes").operations
    for index, (args, op) in enumerate(operations):
        if op == b"q":
            stack.append(fill)
        elif op == b"Q":
            fill = stack.pop()
        elif op in (b"g", b"rg", b"k"):
            fill = (op.decode(), tuple(float(a) for a in args))
        elif op in (b"cs", b"sc", b"scn"):
            fill = None  # Pattern/other spaces have no inferred DeviceRGB/Gray reading.
        if op == b"BT":
            block, colours, matrix, start = [], [], None, index
        if block is not None:
            block.append((args, op))
            if op == b"Tm":
                matrix = list(map(float, args))
            if op in (b"Tj", b"TJ", b"'", b'"'):
                colours.append(fill)
        if op == b"ET" and block is not None:
            if colours and len(set(colours)) == 1 and colours[0] in (
                ("rg", (0.6, 0.6, 0.804)), ("g", (0.651,)), ("g", (0.753,))
            ):
                content = ContentStream(None, reader)
                content.operations = block
                isolated = PageObject(reader)
                isolated[NameObject("/Resources")] = page["/Resources"]
                isolated[NameObject("/Contents")] = content
                selected.append({"operator_start":start,"operator_end":index,
                    "text":isolated.extract_text(),"fill":colours[0],"text_matrix":matrix,
                    "font_operators":[[str(v) for v in a] for a,o in block if o == b"Tf"]})
            block = None
    assert not stack
    return {"runs":selected,"operator_counts":dict(Counter(op.decode() for _,op in operations)),
        "extended_graphics_states":{str(k):str(v.get_object()) for k,v in page['/Resources'].get('/ExtGState',{}).items()},
        "text_render_modes":[[str(v) for v in args] for args,op in operations if op==b'Tr']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    module_name='swingset.sources.wsdc_newsletter._colour_review_baseline'
    baseline_file=args.baseline/'src/swingset/sources/wsdc_newsletter/adapter.py'
    spec=importlib.util.spec_from_file_location(module_name,baseline_file)
    baseline=importlib.util.module_from_spec(spec);sys.modules[module_name]=baseline;spec.loader.exec_module(baseline)
    assert baseline.EventsPage.PARSER_VERSION==7 and EventsPage.PARSER_VERSION==8
    ctx=ParseContext('snapshot','watch','https://example.test/','wsdc_newsletter','wsdc_newsletter.events',None,'2026-09-17T00:00:00+00:00')
    comparison=[];proofs=[];changes=[]
    fixtures=Path('src/swingset/sources/wsdc_newsletter/fixtures')
    for path in sorted(fixtures.glob('*.pdf')):
        body=path.read_bytes();extract=EventsPage().extract(body)
        before=baseline.EventsPage().parse(extract,ctx);after=EventsPage().parse(extract,ctx)
        assert len(before.observations)==len(after.observations)
        diffs=[]
        for old,new in zip(before.observations,after.observations,strict=True):
            if old!=new:
                diffs.append({'before':asdict(old.payload),'after':asdict(new.payload)})
        assert before.legitimate_empty==after.legitimate_empty
        assert [asdict(w) for w in before.warnings if w.code!='newsletter_colour_unverified']==[asdict(w) for w in after.warnings if w.code!='newsletter_colour_unverified']
        if not diffs:assert before==after
        entry={'fixture':str(path),'body_sha256':sha(body),'snapshot_metadata_sha256':sha(path.with_suffix('.json').read_bytes()),'observations_before':len(before.observations),'observations_after':len(after.observations),'legitimate_empty':after.legitimate_empty,'changed_rows':diffs,'warnings_before':[asdict(w) for w in before.warnings],'warnings_after':[asdict(w) for w in after.warnings]}
        comparison.append(entry);changes+=diffs
        labels=reviewed_colours(extract['body_sha256'],extract['pages'])
        if labels:
            reader=PdfReader(path,strict=True)
            pages=[{'page':n,**direct_text_colour(p,reader)} for n,p in enumerate(reader.pages,1)]
            assert all(not p['text_render_modes'] for p in pages)
            proofs.append({'fixture':str(path),'body_sha256':sha(body),'extracted_pages_sha256':sha(json.dumps(extract['pages'],ensure_ascii=False,separators=(',',':')).encode()),'pages':pages,'dispositions':[{'page':k[0],'name':k[1],'start':k[2],'end':k[3],'raw_type':v} for k,v in labels.items()]})
    assert len(comparison)==21 and len(changes)==5 and len(proofs)==4
    report={'format':'newsletter-exact-colour-review-v1','recorded_at':datetime.now(timezone.utc).isoformat(),'baseline_source':str(args.baseline),'baseline_parser_file_sha256':sha(baseline_file.read_bytes()),'baseline_source_receipt_sha256':sha((args.baseline/'extension-source.json').read_bytes()),'current_files':{str(p):sha(p.read_bytes()) for p in [Path('src/swingset/sources/wsdc_newsletter/adapter.py'),Path('src/swingset/sources/wsdc_newsletter/colour_review.py'),Path('tests/test_newsletter_colour_review.py')]},'fixture_count':21,'changed_rows':5,'comparison':comparison,'operator_evidence':proofs,'limitations':['Exact-body, exact-extract, page/name/date-specific readings only; no general PDF colour classifier.','Isolated BT/ET blocks use original font resources and byte-string decoding. Text matrices identify source positions; no guessed dates.','Pattern colours are unknown; this review records only the exact DeviceRGB/DeviceGray runs and graphics states.','Text rendering mode defaults to filled text; no Tr operator appears in these reviewed pages.','Other sidebar rows and warnings remain unassessed; Member Activity is raw source vocabulary, not inferred trial or registry status.'],'network_requests':0,'production_operations':0,'passed':True}
    (args.output/'review.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.output/'review.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'fixtures':len(comparison),'rows_changed':len(changes),'output':str(args.output)}))

if __name__=='__main__':main()
