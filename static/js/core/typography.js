// Shared typography primitives used by Manuscript, Reviews, Canvas, and Maps.

const MANUSCRIPT_TEXT_SIZE_DEFAULT=14;
const MANUSCRIPT_TEXT_SIZE_MIN=11;
const MANUSCRIPT_TEXT_SIZE_MAX=28;
function normalizeManuscriptTextSize(value){
  const size=Number(value);if(!Number.isFinite(size))return MANUSCRIPT_TEXT_SIZE_DEFAULT;
  return Math.max(MANUSCRIPT_TEXT_SIZE_MIN,Math.min(MANUSCRIPT_TEXT_SIZE_MAX,Math.round(size*2)/2));
}
function formatManuscriptTextSize(value){const size=normalizeManuscriptTextSize(value);return `${Number.isInteger(size)?size:size.toFixed(1)} px`}
const MANUSCRIPT_FONTS={
  arial:{label:'Arial',css:'Arial, sans-serif'},
  times:{label:'Times New Roman',css:"'Times New Roman', Times, serif"},
  verdana:{label:'Verdana',css:'Verdana, Geneva, sans-serif'},
  courier:{label:'Courier New',css:"'Courier New', Courier, monospace"},
};
