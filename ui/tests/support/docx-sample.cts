// A small Word file (.docx) built with the vendored JSZip (ui/vendor/jszip.min.js), for the review view's scripts: a
// heading, paragraphs, a table, a picture inside the file, and what a hostile file carries: a javascript: link, a
// picture on another site and an embedded HTML chunk with a script. docxSample(paragraphs) gives its bytes.
const path: typeof import('node:path') = require('node:path');

interface ZipLike { file(name: string, data: string | Buffer): void; generateAsync(o: {type: 'nodebuffer'}): Promise<Buffer> }
const JSZip = require(path.join(__dirname, '../../vendor/jszip.min.js')) as new () => ZipLike;

const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const xesc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const run = (t: string): string => `<w:r><w:t xml:space="preserve">${xesc(t)}</w:t></w:r>`;
const para = (t: string, style = ''): string => `<w:p>${style ? `<w:pPr><w:pStyle w:val="${style}"/></w:pPr>` : ''}${run(t)}</w:p>`;
const cell = (t: string): string => `<w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>${para(t)}</w:tc>`;
const picture = (rel: string): string => `<w:p><w:r><w:drawing><wp:inline><wp:extent cx="381000" cy="381000"/><wp:docPr id="1" name="dot"/>`
  + `<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="dot"/><pic:cNvPicPr/></pic:nvPicPr>`
  + `<pic:blipFill><a:blip r:embed="${rel}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="381000" cy="381000"/></a:xfrm>`
  + `<a:prstGeom prst="rect"/></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>`;

async function docxSample(paragraphs: string[], title = 'Pricing memo'): Promise<Buffer> {
  const zip = new JSZip();
  zip.file('[Content_Types].xml', XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    + '<Default Extension="png" ContentType="image/png"/><Default Extension="html" ContentType="text/html"/>'
    + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    + '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>');
  zip.file('_rels/.rels', XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + `<Relationship Id="rId1" Type="${REL}/officeDocument" Target="word/document.xml"/></Relationships>`);
  zip.file('word/_rels/document.xml.rels', XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + `<Relationship Id="rIdS" Type="${REL}/styles" Target="styles.xml"/>`
    + `<Relationship Id="rIdJs" Type="${REL}/hyperlink" Target="javascript:parent.DOCX_LINK=1" TargetMode="External"/>`
    + `<Relationship Id="rIdWeb" Type="${REL}/hyperlink" Target="https://example.com/pricing" TargetMode="External"/>`
    + `<Relationship Id="rIdPic" Type="${REL}/image" Target="media/dot.png"/>`
    + `<Relationship Id="rIdFar" Type="${REL}/image" Target="https://evil.example/pixel.png" TargetMode="External"/>`
    + `<Relationship Id="rIdChunk" Type="${REL}/aFChunk" Target="chunk.html"/></Relationships>`);
  zip.file('word/chunk.html', '<html><body><script>parent.DOCX_CHUNK=1</script><p>chunk</p></body></html>');
  zip.file('word/media/dot.png', PNG);
  zip.file('word/styles.xml', XML + '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    + '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
    + '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    + '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:color w:val="2F5496"/><w:sz w:val="40"/></w:rPr></w:style>'
    + '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="000000"/><w:left w:val="single" w:sz="4" w:color="000000"/><w:bottom w:val="single" w:sz="4" w:color="000000"/><w:right w:val="single" w:sz="4" w:color="000000"/><w:insideH w:val="single" w:sz="4" w:color="000000"/><w:insideV w:val="single" w:sz="4" w:color="000000"/></w:tblBorders></w:tblPr></w:style>'
    + '</w:styles>');
  zip.file('word/document.xml', XML + '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    + ` xmlns:r="${REL}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"`
    + ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'
    + para(title, 'Heading1') + paragraphs.map(p => para(p)).join('')
    + `<w:p><w:hyperlink r:id="rIdWeb">${run('Pricing page')}</w:hyperlink></w:p>`
    + `<w:p><w:hyperlink r:id="rIdJs">${run('Open me')}</w:hyperlink></w:p>`
    + '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="6000" w:type="dxa"/></w:tblPr><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>'
    + `<w:tr>${cell('Plan')}${cell('Price')}</w:tr><w:tr>${cell('Pro')}${cell('$49 a month')}</w:tr></w:tbl>`
    + picture('rIdPic') + picture('rIdFar') + '<w:altChunk r:id="rIdChunk"/>'
    + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    + '</w:body></w:document>');
  return zip.generateAsync({type: 'nodebuffer'});
}
const api = {docxSample};
export type Api = typeof api;
module.exports = api;
