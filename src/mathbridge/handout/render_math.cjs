/* Local-only components, standalone glyph paths, no autoload/require extensions. */
const {mathjax} = require('mathjax-full/js/mathjax.js');
const {TeX} = require('mathjax-full/js/input/tex.js');
const {SVG} = require('mathjax-full/js/output/svg.js');
const {liteAdaptor} = require('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = require('mathjax-full/js/handlers/html.js');
require('mathjax-full/js/input/tex/base/BaseConfiguration.js');
require('mathjax-full/js/input/tex/ams/AmsConfiguration.js');
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const input = new TeX({packages: ['base', 'ams'], maxBuffer: 4096, maxMacros: 200,
  formatError: (_jax, error) => { throw new Error(error.message); }});
const doc = mathjax.document('', {InputJax: input, OutputJax: new SVG({fontCache: 'none'})});
let body = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {body += chunk; if (body.length > 1000000) process.exit(2);});
process.stdin.on('end', () => {
  try {
    const formulas = JSON.parse(body);
    if (!Array.isArray(formulas) || formulas.length > 2000) throw Error('Batch limit');
    const result = formulas.map(latex => {
      if (typeof latex !== 'string' || latex.length > 4096) throw Error('Length limit');
      if (/\\(?:require|autoload|def|newcommand|href|url|html|style|input|include|write|csname)\b|(?:https?|file|data|javascript):|[<>%]/i.test(latex)) throw Error('Unsafe formula');
      const node = doc.convert(latex, {display: true, em: 16, ex: 8, containerWidth: 700});
      const svg = adaptor.tags(node, 'svg')[0];
      if (!svg) throw Error('No SVG output');
      return adaptor.outerHTML(svg);
    });
    process.stdout.write(JSON.stringify(result));
  } catch (error) { process.stderr.write(String(error.message)); process.exitCode = 1; }
});
