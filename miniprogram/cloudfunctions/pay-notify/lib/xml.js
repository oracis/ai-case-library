/**
 * 极简 XML 字段提取（零依赖）。
 * 发货推送是微信平台推来的 XML，只需要按 key 取值，
 * 不值得为此引入 xml2js。
 */

/** 取 <key>value</key> 或 <key><![CDATA[value]]></key> 里的 value */
function xmlValue(xml, key) {
  if (!xml || !key) return '';
  const re = new RegExp('<' + key + '[^>]*>\\s*(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?\\s*</' + key + '>');
  const m = xml.match(re);
  return m ? m[1].trim() : '';
}

module.exports = { xmlValue };
