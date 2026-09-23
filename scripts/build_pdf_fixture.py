"""Generate the committed synthetic bilingual PDF used by ingestion QA."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import argparse

path=Path('tests/fixtures/policies/materials.pdf')
path.parent.mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser();parser.add_argument('--font',required=True);args=parser.parse_args()
pdfmetrics.registerFont(TTFont('FixtureCJK',args.font,subfontIndex=0))
c=canvas.Canvas(str(path),pagesize=(595,842),invariant=1)
c.setTitle('Synthetic policy - materials and timing')
pages=[('退货材料说明',['办理退货时，应保留订单编号与商品照片。','资料缺失时先补充材料，不代表已获退款批准。']),
       ('处理时限说明',['本样例没有承诺退款到账时限。','退款资格仍须依据已审核政策与真实订单事实核验。'])]
for index,(title,lines) in enumerate(pages,1):
    c.setFillColorRGB(.1,.18,.3);c.setFont('FixtureCJK',22);c.drawString(54,760,title)
    c.setFillColorRGB(.2,.2,.2);c.setFont('FixtureCJK',13)
    for n,line in enumerate(lines):c.drawString(54,700-n*30,line)
    c.setFont('Helvetica',11);c.drawString(54,590,'Synthetic QA document. No payment authorization.')
    c.drawString(54,48,f'Physical page {index} / 2');c.showPage()
c.save()
path.with_suffix('.pdf.json').write_text('{"id":"materials-pdf-v1","title":"PDF材料与时限样例","version":"1"}\n',encoding='utf-8')
print(path)
